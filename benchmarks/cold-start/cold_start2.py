import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# PARÂMETROS FIXOS DO EXPERIMENTO
# ============================================================

EXPECTED_PYTHON = (3, 10, 12)

OFFICIAL_REPETITIONS = 30
PILOT_REPETITIONS = 2

STABILIZATION_SECONDS = 60.0

STARTUP_TIMEOUT_SECONDS = 10.0
PROBE_TIMEOUT_SECONDS = 0.200
PROBE_INTERVAL_SECONDS = 0.010

COOLDOWN_SECONDS = 5.0

STOP_TIMEOUT_SECONDS = 5.0
PORT_CLOSE_TIMEOUT_SECONDS = 3.0

EXPECTED_HTTP_STATUS = 200
EXPECTED_BODY = b'{"status":"ok"}'


# ============================================================
# CAMINHOS
# ============================================================

PROJECT_DIR = Path(
    "/home/vagrant/rust-benchmark-api"
)

EXPECTED_SCRIPT_PATH = (
    PROJECT_DIR
    / "benchmarks"
    / "cold-start"
    / "cold_start.py"
)

SCRIPT_DIR = EXPECTED_SCRIPT_PATH.parent

CONDITIONING_DIR = (
    SCRIPT_DIR
    / "conditioning"
)

PILOT_DIR = (
    SCRIPT_DIR
    / "pilot"
)

OFFICIAL_DIR = (
    SCRIPT_DIR
    / "results"
    / "official"
)

DISCARDED_DIR = (
    SCRIPT_DIR
    / "results"
    / "discarded"
)


# ============================================================
# ARTEFATO LOCAL
# ============================================================

LOCAL_BINARY = (
    PROJECT_DIR
    / "target"
    / "release"
    / "rust-benchmark-api"
)

LOCAL_SHA256 = (
    "98743849fdbaa84be00ad75d6c680314"
    "8600dbffe74d0858e83944c59747b8b3"
)


# ============================================================
# ARTEFATO DOCKER
# ============================================================

DOCKER_IMAGE = (
    "rust-benchmark-api:0.1.0-8503f41a"
)

DOCKER_IMAGE_ID = (
    "sha256:"
    "1df15a150099d4d5258f2b68851debcf"
    "dd9e6295766a1f9ff76c347cab948015"
)

DOCKER_BINARY_SHA256 = (
    "8aeee772194e00fce4d1971206f98cd4"
    "fe3aadcdf0fad87977c05ede849ef194"
)


# ============================================================
# ARTEFATO WASM
# ============================================================

WASM_ARTIFACT = (
    PROJECT_DIR
    / "target"
    / "wasm32-wasip2"
    / "release"
    / "rust_benchmark_api.wasm"
)

WASM_SHA256 = (
    "e168ee966b7e4c681687e2b3f3d2916e"
    "2d4defd6c836ffc1d258d6a577c3796d"
)

SPIN_TOML = (
    PROJECT_DIR
    / "spin.toml"
)

SPIN_TOML_SHA256 = (
    "549c60ca843eb894b76995c705b65797"
    "9e11c03d9c6aeb9d261d4ee67821de09"
)

EXPECTED_SPIN_VERSION = (
    "spin 4.1.0 (c0b3726 2026-08-25)"
)


# ============================================================
# CONFIGURAÇÃO DAS TRÊS VMs
# ============================================================

CONFIG = {
    "benchmark-local": {
        "environment": "local",
        "host": "192.168.56.10",
        "port": 3000,
        "url":
            "http://192.168.56.10:3000/health",
        "artifact_sha256":
            LOCAL_SHA256,
    },

    "benchmark-docker": {
        "environment": "docker",
        "host": "192.168.56.11",
        "port": 3000,
        "url":
            "http://192.168.56.11:3000/health",
        "artifact_sha256":
            DOCKER_BINARY_SHA256,
        "image_id":
            DOCKER_IMAGE_ID,
    },

    "benchmark-wasm": {
        "environment": "wasm",
        "host": "192.168.56.12",
        "port": 3000,
        "url":
            "http://192.168.56.12:3000/health",
        "artifact_sha256":
            WASM_SHA256,
    },
}


# ============================================================
# CLIENTE HTTP SEM PROXY
# ============================================================

HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({})
)


# ============================================================
# ERRO PROCEDURAL
# ============================================================

class ProceduralError(RuntimeError):
    pass


# ============================================================
# TEMPO E HASH
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat(
        timespec="microseconds"
    )


def compact_utc_now():
    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )


def sha256_file(path):
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(
                1024 * 1024
            ),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def harness_sha256():
    return sha256_file(
        Path(__file__).resolve()
    )


# ============================================================
# GRAVAÇÃO SEGURA
# ============================================================


def write_json_exclusive(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary_path = None
    directory_fd = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)

            json.dump(
                data,
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )

            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        # Publicação exclusiva e atômica.
        # Falha se o destino já existir.
        os.link(temporary_path, path)

        temporary_path.unlink()
        temporary_path = None

        # Sincroniza a alteração no diretório.
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY,
        )
        os.fsync(directory_fd)

    finally:
        if directory_fd is not None:
            os.close(directory_fd)

        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)



# ============================================================
# VERIFICAÇÃO DA PORTA
# ============================================================

def port_is_open(
    host,
    port,
):
    try:
        with socket.create_connection(
            (host, port),
            timeout=(
                PROBE_TIMEOUT_SECONDS
            ),
        ):
            return True

    except OSError:
        return False


def wait_port_closed(
    host,
    port,
):
    deadline = (
        time.monotonic()
        + PORT_CLOSE_TIMEOUT_SECONDS
    )

    while time.monotonic() < deadline:

        if not port_is_open(
            host,
            port,
        ):
            return True

        time.sleep(
            PROBE_INTERVAL_SECONDS
        )

    return not port_is_open(
        host,
        port,
    )


# ============================================================
# VERIFICAÇÃO DE PROCESSOS
# ============================================================

def read_process_args():

    for entry in Path(
        "/proc"
    ).iterdir():

        if not entry.name.isdigit():
            continue

        try:
            raw = (
                entry
                / "cmdline"
            ).read_bytes()

        except (
            FileNotFoundError,
            PermissionError,
            ProcessLookupError,
        ):
            continue

        args = [
            item.decode(
                "utf-8",
                errors="replace",
            )
            for item
            in raw.split(b"\0")
            if item
        ]

        if args:
            yield args


def local_process_exists():

    for args in read_process_args():

        if any(
            arg.endswith(
                "/rust-benchmark-api"
            )
            for arg in args
        ):
            return True

    return False


def spin_process_exists():

    for args in read_process_args():

        executable = Path(
            args[0]
        ).name

        if (
            executable == "spin"
            and "up" in args[1:]
        ):
            return True

    return False


# ============================================================
# COMANDOS DOCKER
# ============================================================

def docker_command(
    args,
    timeout=5.0,
):
    try:
        return subprocess.run(
            [
                "sudo",
                "-n",
                "docker",
                *args,
            ],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired as exc:

        raise ProceduralError(
            "Comando Docker excedeu "
            "o tempo operacional."
        ) from exc


def docker_container_exists(
    name,
):
    result = docker_command(
        [
            "container",
            "inspect",
            name,
        ]
    )

    if result.returncode == 0:
        return True

    error_text = (
        result.stderr
        + result.stdout
    )

    if (
        "No such object"
        in error_text
        or
        "No such container"
        in error_text
    ):
        return False

    raise ProceduralError(
        "Não foi possível verificar "
        f"o container {name}: "
        f"{error_text.strip()}"
    )


def docker_container_state(
    name,
):
    result = docker_command(
        [
            "container",
            "inspect",
            name,
            "--format",
            (
                "{{.Id}}|"
                "{{.State.Running}}|"
                "{{.State.Status}}|"
                "{{.State.ExitCode}}"
            ),
        ]
    )

    if result.returncode != 0:

        raise ProceduralError(
            "Não foi possível "
            "inspecionar o container "
            f"{name}: "
            f"{result.stderr.strip()}"
        )

    parts = (
        result.stdout
        .strip()
        .split("|")
    )

    if len(parts) != 4:

        raise ProceduralError(
            "Saída inesperada "
            "do docker inspect."
        )

    return {
        "container_id":
            parts[0],

        "running":
            parts[1] == "true",

        "status":
            parts[2],

        "exit_code":
            int(parts[3]),
    }



def docker_stop_and_remove(name, expected_id):
    # Aceita somente nomes criados pelo harness.
    if not name.startswith("rust-benchmark-api-coldstart-"):
        raise ProceduralError(
            "Nome de container não autorizado."
        )

    # Docker run -d retorna um ID hexadecimal de 64 caracteres.
    if (
        not isinstance(expected_id, str)
        or len(expected_id) != 64
        or any(c not in "0123456789abcdef" for c in expected_id)
    ):
        raise ProceduralError(
            "ID Docker inválido ou não confirmado. "
            "Nenhuma remoção será realizada."
        )

    if not docker_container_exists(name):
        raise ProceduralError(
            f"Container esperado não encontrado: {name}"
        )

    state = docker_container_state(name)

    # Confirma que o nome ainda corresponde ao container criado.
    if state["container_id"] != expected_id:
        raise ProceduralError(
            "Identidade do container não confere. "
            "Operação de limpeza abortada."
        )

    forced = False

    if state["running"]:
        stopped = docker_command(
            ["stop", "--time", str(int(STOP_TIMEOUT_SECONDS)), expected_id],
            timeout=STOP_TIMEOUT_SECONDS + 2.0,
        )

        if stopped.returncode != 0:
            forced = True

            killed = docker_command(
                ["kill", expected_id],
                timeout=3.0,
            )

            if killed.returncode != 0:
                raise ProceduralError(
                    "Não foi possível parar o container esperado."
                )

    removed = docker_command(
        ["rm", expected_id],
        timeout=3.0,
    )

    if removed.returncode != 0:
        raise ProceduralError(
            "Não foi possível remover o container esperado: "
            f"{removed.stderr.strip()}"
        )

    return {
        "forced": forced,
        "container_id": expected_id,
        "status_before_cleanup": state["status"],
        "exit_code_before_cleanup": state["exit_code"],
    }



# ============================================================
# CAMINHOS DOS RESULTADOS
# ============================================================

def planned_output_path(
    mode,
    environment,
    repetition,
):
    filename = (
        f"r{repetition:02d}.json"
    )

    if mode == "pilot":

        return (
            PILOT_DIR
            / environment
            / filename
        )

    return (
        OFFICIAL_DIR
        / environment
        / filename
    )


def conditioning_output_path(
    mode,
    environment,
):
    return (
        CONDITIONING_DIR
        / environment
        / f"{mode}.json"
    )


def discarded_output_path(
    environment,
    label,
):
    safe_label = (
        label.replace(
            "/",
            "_",
        )
    )

    return (
        DISCARDED_DIR
        / environment
        / (
            f"{safe_label}-"
            f"{compact_utc_now()}.json"
        )
    )


def docker_container_name(
    label,
):
    return (
        "rust-benchmark-api-"
        "coldstart-"
        + label.replace(
            "_",
            "-",
        )
    )


# ============================================================
# PROTEÇÃO CONTRA SOBRESCRITA
# ============================================================

def verify_no_planned_outputs(
    mode,
    environment,
    repetitions,
):
    conditioning = (
        conditioning_output_path(
            mode,
            environment,
        )
    )

    if conditioning.exists():

        raise ProceduralError(
            "Resultado de "
            "condicionamento já existe: "
            f"{conditioning}"
        )

    for repetition in range(
        1,
        repetitions + 1,
    ):
        path = planned_output_path(
            mode,
            environment,
            repetition,
        )

        if path.exists():

            raise ProceduralError(
                "Resultado já existe: "
                f"{path}"
            )


# ============================================================
# PREFLIGHT
# ============================================================

def verify_preflight(
    config,
    mode,
    repetitions,
):

    if os.geteuid() == 0:

        raise ProceduralError(
            "O harness não deve ser "
            "executado como root."
        )

    if (
        sys.version_info[:3]
        != EXPECTED_PYTHON
    ):
        raise ProceduralError(
            "Versão inesperada "
            "do Python: "
            f"{sys.version.split()[0]}"
        )

    if (
        Path(__file__).resolve()
        != EXPECTED_SCRIPT_PATH
    ):
        raise ProceduralError(
            "O harness não está "
            "no caminho esperado: "
            f"{EXPECTED_SCRIPT_PATH}"
        )

    if not PROJECT_DIR.is_dir():

        raise ProceduralError(
            "Diretório do projeto "
            "não encontrado."
        )

    if port_is_open(
        config["host"],
        config["port"],
    ):
        raise ProceduralError(
            "A porta 3000 já está "
            "respondendo antes "
            "da campanha."
        )

    environment = (
        config["environment"]
    )

    # ---------------- LOCAL ----------------

    if environment == "local":

        if (
            not LOCAL_BINARY.is_file()
            or
            not os.access(
                LOCAL_BINARY,
                os.X_OK,
            )
        ):
            raise ProceduralError(
                "Binário Local ausente "
                "ou não executável."
            )

        if (
            sha256_file(
                LOCAL_BINARY
            )
            != LOCAL_SHA256
        ):
            raise ProceduralError(
                "SHA-256 do binário "
                "Local não confere."
            )

        if local_process_exists():

            raise ProceduralError(
                "Processo Local antigo "
                "ainda está rodando."
            )

    # ---------------- DOCKER ----------------

    elif environment == "docker":

        image = docker_command(
            [
                "image",
                "inspect",
                DOCKER_IMAGE,
                "--format",
                "{{.Id}}",
            ]
        )

        if image.returncode != 0:

            raise ProceduralError(
                "Imagem Docker esperada "
                "não está disponível: "
                f"{image.stderr.strip()}"
            )

        if (
            image.stdout.strip()
            != DOCKER_IMAGE_ID
        ):
            raise ProceduralError(
                "Image ID Docker "
                "não confere."
            )

    # ---------------- WASM ----------------

    elif environment == "wasm":

        if not WASM_ARTIFACT.is_file():

            raise ProceduralError(
                "Artefato WASM ausente."
            )

        if (
            sha256_file(
                WASM_ARTIFACT
            )
            != WASM_SHA256
        ):
            raise ProceduralError(
                "SHA-256 do WASM "
                "não confere."
            )
            
        if not SPIN_TOML.is_file():

            raise ProceduralError(
                "spin.toml ausente."
            )

        if (
            sha256_file(
                SPIN_TOML
            )
            != SPIN_TOML_SHA256
        ):
            raise ProceduralError(
                "SHA-256 do spin.toml "
                "não confere."
            )

        try:
            spin = subprocess.run(
                [
                    "spin",
                    "--version",
                ],
                capture_output=True,
                text=True,
                timeout=5.0,
            )

        except (
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ) as exc:

            raise ProceduralError(
                "Não foi possível "
                "executar spin --version."
            ) from exc

        if (
            spin.returncode != 0
            or
            spin.stdout.strip()
            != EXPECTED_SPIN_VERSION
        ):
            raise ProceduralError(
                "Versão inesperada "
                "do Spin: "
                f"{spin.stdout.strip()}"
            )

        if spin_process_exists():

            raise ProceduralError(
                "Processo Spin antigo "
                "ainda está rodando."
            )

    verify_no_planned_outputs(
        mode,
        environment,
        repetitions,
    )


# ============================================================
# ESTADO LIMPO ANTES DE CADA REPETIÇÃO
# ============================================================

def ensure_clean_state(
    config,
    label,
):

    if port_is_open(
        config["host"],
        config["port"],
    ):
        raise ProceduralError(
            "A porta 3000 já está "
            "respondendo."
        )

    environment = (
        config["environment"]
    )

    if environment == "local":

        if local_process_exists():

            raise ProceduralError(
                "Processo Local "
                "já está rodando."
            )

    elif environment == "wasm":

        if spin_process_exists():

            raise ProceduralError(
                "Spin já está rodando."
            )

    elif environment == "docker":

        name = docker_container_name(
            label
        )

        if docker_container_exists(
            name
        ):
            raise ProceduralError(
                "O container planejado "
                f"já existe: {name}"
            )


# ============================================================
# COMANDOS DE INICIALIZAÇÃO
# ============================================================

def build_start_spec(
    config,
    label,
):

    environment = (
        config["environment"]
    )

    # ---------------- LOCAL ----------------

    if environment == "local":

        return {
            "kind":
                "process",

            "command": [
                str(LOCAL_BINARY),
            ],

            "environment_updates": {
                "RUST_BENCHMARK_ADDRESS":
                    "192.168.56.10:3000",
            },
        }

    # ---------------- DOCKER ----------------

    if environment == "docker":

        name = docker_container_name(
            label
        )

        return {
            "kind":
                "docker",

            "container_name":
                name,

            "command": [
                "sudo",
                "-n",
                "docker",
                "run",
                "-d",

                "--name",
                name,

                "--pull=never",

                "--read-only",

                "--cap-drop=ALL",

                "--security-opt",
                "no-new-privileges:true",

                "--pids-limit=128",

                "--network",
                "bridge",

                "-p",
                "192.168.56.11:3000:3000",

                DOCKER_IMAGE,
            ],

            "environment_updates":
                {},
        }

    # ---------------- WASM ----------------

    return {
        "kind":
            "process",

        "command": [
            "spin",
            "up",
            "--listen",
            "192.168.56.12:3000",
        ],

        "environment_updates":
            {},
    }


# ============================================================
# INICIAR RUNTIME
# ============================================================

def start_runtime(
    spec,
):

    if spec["kind"] == "docker":

        try:
            process = subprocess.Popen(
                spec["command"],
                cwd=PROJECT_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

        except OSError as exc:

            raise ProceduralError(
                "Não foi possível "
                "iniciar docker run."
            ) from exc

        return {
            "kind":
                "docker",

            "process":
                process,

            "container_name":
                spec["container_name"],

            "docker_stdout":
                None,

            "docker_stderr":
                None,
        }

    env = os.environ.copy()

    env.update(
        spec["environment_updates"]
    )

    try:
        process = subprocess.Popen(
            spec["command"],
            cwd=PROJECT_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    except OSError as exc:

        raise ProceduralError(
            "Não foi possível "
            "iniciar o runtime."
        ) from exc

    return {
        "kind":
            "process",

        "process":
            process,
    }


# ============================================================
# CAPTURAR RESULTADO DO docker run
# ============================================================

def capture_docker_cli(
    runtime,
):

    process = runtime["process"]

    if (
        runtime["docker_stdout"]
        is not None
    ):
        return

    if process.poll() is None:
        return

    stdout, stderr = (
        process.communicate()
    )

    runtime["docker_stdout"] = (
        stdout.strip()
    )

    runtime["docker_stderr"] = (
        stderr.strip()
    )


# ============================================================
# ENCERRAR PROCESSO LOCAL / SPIN
# ============================================================

def process_group_has_members(group_id):
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue

        try:
            stat = (entry / "stat").read_text(
                encoding="utf-8"
            )

            # Campos após o nome do processo.
            fields = stat[stat.rfind(")") + 1:].split()

            if len(fields) < 4:
                raise ProceduralError(
                    "Formato inesperado em /proc/PID/stat."
                )

            state = fields[0]
            process_group = int(fields[2])
            session_id = int(fields[3])

            if (
                process_group == group_id
                and session_id == group_id
                and state not in ("Z", "X", "x")
            ):
                return True

        except (FileNotFoundError, ProcessLookupError):
            continue

        except (PermissionError, ValueError) as exc:
            raise ProceduralError(
                "Não foi possível verificar os processos."
            ) from exc

    return False

def terminate_process_group(process):
    group_id = process.pid
    forced = False

    # Confirma a identidade da sessão se o líder ainda existe.
    if process.poll() is None:
        try:
            if (
                os.getpgid(process.pid) != group_id
                or os.getsid(process.pid) != group_id
            ):
                raise ProceduralError(
                    "Identidade do grupo de processos inesperada."
                )
        except ProcessLookupError:
            pass

    # Encerra o grupo mesmo que o processo principal
    # já tenha terminado, desde que ainda existam membros.
    if process_group_has_members(group_id):
        try:
            os.killpg(group_id, signal.SIGTERM)
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS

    while True:
        process.poll()

        if not process_group_has_members(group_id):
            break

        if time.monotonic() >= deadline:
            forced = True

            try:
                os.killpg(group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass

            break

        time.sleep(0.05)

    # Confirma que nenhum membro do grupo permaneceu.
    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS

    while True:
        process.poll()

        if not process_group_has_members(group_id):
            break

        if time.monotonic() >= deadline:
            raise ProceduralError(
                "Processos residuais encontrados. "
                "Verificação manual necessária."
            )

        time.sleep(0.05)

    try:
        process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ProceduralError(
            "Processo principal não encerrou corretamente."
        ) from exc

    return {
        "forced": forced,
        "returncode": process.returncode,
    }


# ============================================================
# CLEANUP DO RUNTIME
# ============================================================

def cleanup_runtime(
    runtime,
):

    if runtime is None:

        return {
            "forced":
                False,

            "runtime_returncode":
                None,

            "container_id":
                None,

            "container_status_before_cleanup":
                None,

            "container_exit_code_before_cleanup":
                None,
        }

    # ---------------- LOCAL / WASM ----------------

    if runtime["kind"] == "process":

        cleanup = (
            terminate_process_group(
                runtime["process"]
            )
        )

        return {
            "forced":
                cleanup["forced"],

            "runtime_returncode":
                cleanup["returncode"],

            "container_id":
                None,

            "container_status_before_cleanup":
                None,

            "container_exit_code_before_cleanup":
                None,
        }

    # ---------------- DOCKER ----------------

    
    process = runtime["process"]
    forced = False

    # A medição já terminou.
    # Aguarda a conclusão do comando Docker sem afetar t0/t1.
    if runtime["docker_stdout"] is None:
        try:
            stdout, stderr = process.communicate(
                timeout=STARTUP_TIMEOUT_SECONDS
            )

            runtime["docker_stdout"] = stdout.strip()
            runtime["docker_stderr"] = stderr.strip()

        except subprocess.TimeoutExpired as exc:
            # Encerra somente o processo CLI criado pelo harness.
            process.kill()
            process.communicate()

            raise ProceduralError(
                "O comando docker run não terminou no prazo. "
                "Um container pode ter sido criado. "
                "Verificação manual necessária antes de prosseguir."
            ) from exc


    expected_id = runtime["docker_stdout"]

    if process.returncode != 0 or not expected_id:
        raise ProceduralError(
            "Não foi possível confirmar o ID do "
            "container criado pelo docker run. "
            "Nenhuma remoção automática será realizada."
        )

    container_cleanup = docker_stop_and_remove(
        runtime["container_name"],
        expected_id,
    )

    forced = (
        forced
        or
        container_cleanup[
            "forced"
        ]
    )

    return {
        "forced":
            forced,

        "runtime_returncode":
            process.returncode,

        "container_id":
            container_cleanup[
                "container_id"
            ],

        "container_status_before_cleanup":
            container_cleanup[
                "status_before_cleanup"
            ],

        "container_exit_code_before_cleanup":
            container_cleanup[
                "exit_code_before_cleanup"
            ],
    }


# ============================================================
# UM PROBE HTTP
# ============================================================

def probe_once(
    url,
    timeout,
):

    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept":
                "application/json",

            "Connection":
                "close",
        },
    )

    probe_start_ns = (
        time.monotonic_ns()
    )

    status = None
    body = b""
    content_type = None
    error = None

    try:
        with HTTP.open(
            request,
            timeout=timeout,
        ) as response:

            status = (
                response.status
            )

            content_type = (
                response.headers.get(
                    "Content-Type"
                )
            )

            body = response.read(
                4096
            )

    except urllib.error.HTTPError as exc:

        status = exc.code

        if exc.headers:
            content_type = (
                exc.headers.get(
                    "Content-Type"
                )
            )

        body = exc.read(
            4096
        )

    except urllib.error.URLError as exc:

        error = (
            "URLError:"
            + str(exc.reason)
        )

    except TimeoutError:

        error = "TimeoutError"

    except OSError as exc:

        error = (
            "OSError:"
            + str(exc)
        )

    probe_end_ns = (
        time.monotonic_ns()
    )

    return {
        "valid": (
            status
            == EXPECTED_HTTP_STATUS
            and
            body
            == EXPECTED_BODY
        ),

        "status":
            status,

        "body":
            body,

        "content_type":
            content_type,

        "error":
            error,

        "start_ns":
            probe_start_ns,

        "end_ns":
            probe_end_ns,

        "duration_ns": (
            probe_end_ns
            - probe_start_ns
        ),
    }


# ============================================================
# ESTRUTURA BASE DE UM RESULTADO
# ============================================================

def base_result(
    config,
    phase,
    repetition,
    label,
    script_hash,
    spec,
):

    return {
        "environment":
            config["environment"],

        "hostname":
            socket.gethostname(),

        "phase":
            phase,

        "repetition":
            repetition,

        "wall_timestamp_start":
            None,

        "wall_timestamp_ready":
            None,

        "monotonic_start_ns":
            None,

        "monotonic_ready_ns":
            None,

        "monotonic_measurement_end_ns":
            None,

        "cold_start_ns":
            None,

        "cold_start_ms":
            None,

        "elapsed_ms":
            None,

        "probe_count":
            0,

        "max_probe_gap_ms":
            None,

        "max_probe_duration_ms":
            None,

        "probe_attempts":
            [],

        "http_status":
            None,

        "response_body":
            None,

        "content_type":
            None,

        "success":
            False,

        "failure_reason":
            None,

        "runtime_command":
            spec["command"],

        "runtime_environment":
            spec[
                "environment_updates"
            ],

        "runtime_identifier":
            None,

        "runtime_returncode_before_cleanup":
            None,

        "artifact_sha256":
            config[
                "artifact_sha256"
            ],

        "docker_image_id":
            config.get(
                "image_id"
            ),

        "harness_sha256":
            script_hash,

        "python_version":
            sys.version.split()[0],

        "startup_timeout_ms":
            int(
                STARTUP_TIMEOUT_SECONDS
                * 1000
            ),

        "probe_timeout_ms":
            int(
                PROBE_TIMEOUT_SECONDS
                * 1000
            ),

        "probe_interval_ms":
            int(
                PROBE_INTERVAL_SECONDS
                * 1000
            ),

        "cooldown_seconds":
            COOLDOWN_SECONDS,

        "stabilization_seconds":
            STABILIZATION_SECONDS,

        "cleanup_forced":
            False,

        "cleanup_runtime_returncode":
            None,

        "container_status_before_cleanup":
            None,

        "container_exit_code_before_cleanup":
            None,

        "procedural_error":
            None,

        "label":
            label,
    }


# ============================================================
# UMA MEDIÇÃO COMPLETA
# ============================================================

def run_one(
    config,
    phase,
    repetition,
    label,
    destination,
    script_hash,
):

    spec = build_start_spec(
        config,
        label,
    )

    result = base_result(
        config,
        phase,
        repetition,
        label,
        script_hash,
        spec,
    )

    runtime = None
    procedural_error = None

    try:

        # ----------------------------------------------------
        # ESTADO INICIAL
        # ----------------------------------------------------

        ensure_clean_state(
            config,
            label,
        )

        # ----------------------------------------------------
        # t0
        # ----------------------------------------------------

        result[
            "wall_timestamp_start"
        ] = utc_now()

        start_ns = (
            time.monotonic_ns()
        )

        result[
            "monotonic_start_ns"
        ] = start_ns

        # ----------------------------------------------------
        # INICIA RUNTIME
        # ----------------------------------------------------

        runtime = start_runtime(
            spec
        )

        if runtime["kind"] == "process":

            result[
                "runtime_identifier"
            ] = runtime[
                "process"
            ].pid

        else:

            result[
                "runtime_identifier"
            ] = runtime[
                "container_name"
            ]

        # ----------------------------------------------------
        # DEADLINE GLOBAL
        # ----------------------------------------------------

        deadline_ns = (
            start_ns
            + int(
                STARTUP_TIMEOUT_SECONDS
                * 1_000_000_000
            )
        )

        probe_count = 0

        previous_probe_start_ns = (
            None
        )

        max_probe_gap_ns = 0

        max_probe_duration_ns = 0

        last_probe = None

        failure_reason = None

        ready_ns = None

        # ----------------------------------------------------
        # LOOP DE PROBES
        # ----------------------------------------------------

        while True:

            now_ns = (
                time.monotonic_ns()
            )

            if now_ns >= deadline_ns:

                failure_reason = (
                    "startup_timeout"
                )

                break

            process = runtime[
                "process"
            ]

            # -----------------------------------------------
            # LOCAL / WASM MORREU ANTES DE FICAR PRONTO
            # -----------------------------------------------

            if runtime["kind"] == "process":

                returncode = (
                    process.poll()
                )

                if returncode is not None:

                    result[
                        "runtime_returncode_before_cleanup"
                    ] = returncode

                    failure_reason = (
                        "runtime_exited_before_ready"
                    )

                    break

            # -----------------------------------------------
            # docker run RETORNOU ERRO
            # -----------------------------------------------

            else:

                returncode = (
                    process.poll()
                )

                if returncode is not None:

                    capture_docker_cli(
                        runtime
                    )

                    if returncode != 0:

                        result[
                            "runtime_returncode_before_cleanup"
                        ] = returncode

                        raise ProceduralError(
                            "docker run falhou "
                            f"(codigo {returncode}): "
                            f"{runtime['docker_stderr'] or 'sem detalhes'}"
                        )

            # -----------------------------------------------
            # PROBE
            # -----------------------------------------------

            probe_start_ns = (
                time.monotonic_ns()
            )

            remaining_seconds = (
                deadline_ns
                - probe_start_ns
            ) / 1_000_000_000

            if remaining_seconds <= 0:

                failure_reason = (
                    "startup_timeout"
                )

                break

            timeout = min(
                PROBE_TIMEOUT_SECONDS,
                remaining_seconds,
            )

            probe = probe_once(
                config["url"],
                timeout,
            )

            probe_count += 1

            # -----------------------------------------------
            # GAP ENTRE PROBES
            # -----------------------------------------------

            if (
                previous_probe_start_ns
                is None
            ):
                gap_ns = None

            else:
                gap_ns = (
                    probe["start_ns"]
                    - previous_probe_start_ns
                )

                if (
                    gap_ns
                    > max_probe_gap_ns
                ):
                    max_probe_gap_ns = (
                        gap_ns
                    )

            previous_probe_start_ns = (
                probe["start_ns"]
            )

            # -----------------------------------------------
            # MAIOR DURAÇÃO INDIVIDUAL
            # -----------------------------------------------

            if (
                probe["duration_ns"]
                > max_probe_duration_ns
            ):
                max_probe_duration_ns = (
                    probe["duration_ns"]
                )

            # -----------------------------------------------
            # GUARDA DADOS BRUTOS DO PROBE
            # -----------------------------------------------

            result[
                "probe_attempts"
            ].append({

                "index":
                    probe_count,

                "start_offset_ms": (
                    probe["start_ns"]
                    - start_ns
                ) / 1_000_000,

                "start_gap_ms": (
                    None
                    if gap_ns is None
                    else
                    gap_ns
                    / 1_000_000
                ),

                "duration_ms": (
                    probe[
                        "duration_ns"
                    ]
                    / 1_000_000
                ),

                "http_status":
                    probe["status"],

                "error":
                    probe["error"],

                "valid":
                    probe["valid"],
            })

            last_probe = probe

            # -----------------------------------------------
            # VERIFICA TIMEOUT GLOBAL APÓS O PROBE
            # -----------------------------------------------

            if probe["end_ns"] >= deadline_ns:
                failure_reason = "startup_timeout"
                break

            # -----------------------------------------------
            # PRIMEIRA RESPOSTA VÁLIDA
            # -----------------------------------------------

            if probe["valid"]:

                ready_ns = (
                    probe["end_ns"]
                )

                result[
                    "wall_timestamp_ready"
                ] = utc_now()

                result[
                    "success"
                ] = True

                break

            # -----------------------------------------------
            # VERIFICA DEADLINE NOVAMENTE
            # -----------------------------------------------

            now_ns = (
                time.monotonic_ns()
            )

            if now_ns >= deadline_ns:

                failure_reason = (
                    "startup_timeout"
                )

                break

            # -----------------------------------------------
            # INTERVALO DE 10 ms
            # -----------------------------------------------

            sleep_seconds = min(
                PROBE_INTERVAL_SECONDS,
                (
                    deadline_ns
                    - now_ns
                ) / 1_000_000_000,
            )

            if sleep_seconds > 0:

                time.sleep(
                    sleep_seconds
                )

        # ----------------------------------------------------
        # FINAL DA MEDIÇÃO
        # ----------------------------------------------------

        measurement_end_ns = (
            time.monotonic_ns()
        )

        result[
            "monotonic_measurement_end_ns"
        ] = measurement_end_ns

        result[
            "probe_count"
        ] = probe_count

        result[
            "max_probe_gap_ms"
        ] = (
            max_probe_gap_ns
            / 1_000_000
            if probe_count > 1
            else None
        )

        result[
            "max_probe_duration_ms"
        ] = (
            max_probe_duration_ns
            / 1_000_000
            if probe_count > 0
            else None
        )

        result[
            "elapsed_ms"
        ] = (
            measurement_end_ns
            - start_ns
        ) / 1_000_000

        # ----------------------------------------------------
        # SOMENTE SUCESSO TEM COLD START
        # ----------------------------------------------------

        if ready_ns is not None:

            result[
                "monotonic_ready_ns"
            ] = ready_ns

            result[
                "cold_start_ns"
            ] = (
                ready_ns
                - start_ns
            )

            result[
                "cold_start_ms"
            ] = (
                ready_ns
                - start_ns
            ) / 1_000_000

        else:

            result[
                "failure_reason"
            ] = failure_reason

        # ----------------------------------------------------
        # ÚLTIMA RESPOSTA HTTP OBSERVADA
        # ----------------------------------------------------

        if last_probe is not None:

            result[
                "http_status"
            ] = last_probe[
                "status"
            ]

            result[
                "response_body"
            ] = last_probe[
                "body"
            ].decode(
                "utf-8",
                errors="replace",
            )

            result[
                "content_type"
            ] = last_probe[
                "content_type"
            ]

        # ----------------------------------------------------
        # ERRO DO docker run, SE HOUVER
        # ----------------------------------------------------

        if runtime["kind"] == "docker":

            capture_docker_cli(
                runtime
            )

            if runtime[
                "docker_stderr"
            ]:

                result[
                    "docker_run_stderr"
                ] = runtime[
                    "docker_stderr"
                ]

            else:

                result[
                    "docker_run_stderr"
                ] = ""

    # ========================================================
    # INTERRUPÇÃO MANUAL = ERRO PROCEDURAL
    # ========================================================

    except KeyboardInterrupt:

        procedural_error = (
            "Harness interrompido "
            "manualmente."
        )

    # ========================================================
    # ERRO PROCEDURAL CONHECIDO
    # ========================================================

    except ProceduralError as exc:

        procedural_error = (
            str(exc)
        )

    # ========================================================
    # ERRO INESPERADO
    # ========================================================

    except Exception as exc:

        procedural_error = (
            f"{type(exc).__name__}: "
            f"{exc}"
        )

    # ========================================================
    # CLEANUP SEMPRE OCORRE
    # ========================================================

    try:

        cleanup = cleanup_runtime(
            runtime
        )

        result[
            "cleanup_forced"
        ] = cleanup[
            "forced"
        ]

        result[
            "cleanup_runtime_returncode"
        ] = cleanup[
            "runtime_returncode"
        ]

        if (
            cleanup[
                "container_id"
            ]
            is not None
        ):

            result[
                "runtime_identifier"
            ] = cleanup[
                "container_id"
            ]

        result[
            "container_status_before_cleanup"
        ] = cleanup[
            "container_status_before_cleanup"
        ]

        result[
            "container_exit_code_before_cleanup"
        ] = cleanup[
            "container_exit_code_before_cleanup"
        ]

    except Exception as exc:

        cleanup_error = (
            "Falha de cleanup: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        if procedural_error is None:

            procedural_error = (
                cleanup_error
            )

        else:

            procedural_error += (
                " | "
                + cleanup_error
            )

    # ========================================================
    # PORTA DEVE ESTAR FECHADA
    # ========================================================

    if not wait_port_closed(
        config["host"],
        config["port"],
    ):

        port_error = (
            "A porta 3000 permaneceu "
            "aberta após o cleanup."
        )

        if procedural_error is None:

            procedural_error = (
                port_error
            )

        else:

            procedural_error += (
                " | "
                + port_error
            )

    # ========================================================
    # ERRO PROCEDURAL VAI PARA discarded
    # ========================================================

    if procedural_error is not None:

        result[
            "procedural_error"
        ] = procedural_error

        discard = (
            discarded_output_path(
                config[
                    "environment"
                ],
                label,
            )
        )

        write_json_exclusive(
            discard,
            result,
        )

        raise ProceduralError(
            procedural_error
        )

    # ========================================================
    # RESULTADO NORMAL / FALHA REAL
    # ========================================================

    try:

        write_json_exclusive(
            destination,
            result,
        )

    except Exception as exc:

        result[
            "procedural_error"
        ] = (
            "Falha ao gravar resultado "
            "no destino esperado: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        discard = (
            discarded_output_path(
                config[
                    "environment"
                ],
                label,
            )
        )

        write_json_exclusive(
            discard,
            result,
        )

        raise ProceduralError(
            result[
                "procedural_error"
            ]
        ) from exc

    return result


# ============================================================
# ARGUMENTOS
# ============================================================

def parse_args():

    parser = argparse.ArgumentParser(
        description=(
            "Harness de cold start "
            "do rust-benchmark-api."
        )
    )

    parser.add_argument(
        "mode",
        choices=[
            "pilot",
            "official",
        ],
        help=(
            "pilot executa 2 medições; "
            "official executa 30."
        ),
    )

    return parser.parse_args()


# ============================================================
# MAIN
# ============================================================

def main():

    args = parse_args()

    # --------------------------------------------------------
    # IDENTIFICA AUTOMATICAMENTE A VM
    # --------------------------------------------------------

    hostname = socket.gethostname()

    if hostname not in CONFIG:

        raise SystemExit(
            "ABORTADO: hostname "
            f"não reconhecido: {hostname}"
        )

    config = CONFIG[
        hostname
    ]

    repetitions = (
        PILOT_REPETITIONS
        if args.mode == "pilot"
        else OFFICIAL_REPETITIONS
    )

    # --------------------------------------------------------
    # PREFLIGHT
    # --------------------------------------------------------

    try:

        verify_preflight(
            config,
            args.mode,
            repetitions,
        )

    except ProceduralError as exc:

        raise SystemExit(
            "ABORTADO NO PREFLIGHT: "
            f"{exc}"
        ) from exc

    script_hash = (
        harness_sha256()
    )

    print(
        "Preflight concluído."
    )

    print(
        "Ambiente:",
        config["environment"],
    )

    print(
        "SHA-256 do harness:",
        script_hash,
    )

    # --------------------------------------------------------
    # ESTABILIZAÇÃO DA VM
    # --------------------------------------------------------

    print(
        "Aguardando "
        f"{int(STABILIZATION_SECONDS)} "
        "segundos de estabilização..."
    )

    time.sleep(
        STABILIZATION_SECONDS
    )

    # --------------------------------------------------------
    # CONDICIONAMENTO
    # --------------------------------------------------------

    conditioning_label = (
        f"conditioning-{args.mode}"
    )

    conditioning_destination = (
        conditioning_output_path(
            args.mode,
            config["environment"],
        )
    )

    try:

        conditioning = run_one(
            config=config,
            phase="conditioning",
            repetition=None,
            label=conditioning_label,
            destination=(
                conditioning_destination
            ),
            script_hash=script_hash,
        )

    except ProceduralError as exc:

        raise SystemExit(
            "ABORTADO DURANTE "
            "O CONDICIONAMENTO: "
            f"{exc}"
        ) from exc

    if not conditioning[
        "success"
    ]:

        raise SystemExit(
            "ABORTADO: o "
            "condicionamento não "
            "produziu uma resposta "
            "HTTP válida."
        )

    print(
        "Condicionamento concluído."
    )

    # --------------------------------------------------------
    # COOLDOWN
    # --------------------------------------------------------

    time.sleep(
        COOLDOWN_SECONDS
    )

    # --------------------------------------------------------
    # REPETIÇÕES
    # --------------------------------------------------------

    for repetition in range(
        1,
        repetitions + 1,
    ):

        label = (
            f"{args.mode}-"
            f"r{repetition:02d}"
        )

        destination = (
            planned_output_path(
                args.mode,
                config[
                    "environment"
                ],
                repetition,
            )
        )

        print(
            f"Iniciando {label}..."
        )

        try:

            result = run_one(
                config=config,
                phase=args.mode,
                repetition=repetition,
                label=label,
                destination=destination,
                script_hash=script_hash,
            )

        except ProceduralError as exc:

            raise SystemExit(
                "ABORTADO POR ERRO "
                "PROCEDURAL: "
                f"{exc}"
            ) from exc

        # ----------------------------------------------------
        # MOSTRA RESULTADO SEM ALTERAR ARQUIVO
        # ----------------------------------------------------

        if result["success"]:

            print(
                f"{label}: "
                f"{result['cold_start_ms']:.3f} ms"
            )

        else:

            print(
                f"{label}: falha real "
                f"({result['failure_reason']})"
            )

        # ----------------------------------------------------
        # COOLDOWN ENTRE REPETIÇÕES
        # ----------------------------------------------------

        if repetition < repetitions:

            time.sleep(
                COOLDOWN_SECONDS
            )

    print(
        "Campanha concluída sem "
        "erro procedural."
    )


if __name__ == "__main__":
    main()