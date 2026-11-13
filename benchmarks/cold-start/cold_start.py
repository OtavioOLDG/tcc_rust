#!/usr/bin/env python3
"""Cold start HTTP: processo novo, host/caches aquecidos por condicionamento.

Python 3.10.12 / Linux. Execute somente na VM correspondente ao hostname.
A janela medida inclui Popen e, no Docker, sudo + CLI + criação + rede.
O SHA esperado deste arquivo fica em cold_start.py.sha256, fora da fonte.
"""

import argparse
import errno
import fcntl
import hashlib
import http.client
import json
import math
import os
import platform
import pwd
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


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
DOCKER_COMMAND_TIMEOUT_SECONDS = 5.0
DOCKER_RECONCILE_SECONDS = 3.0
EXPECTED_HTTP_STATUS = 200
EXPECTED_BODY = b'{"status":"ok"}'
SCHEMA_VERSION = 2

PROJECT_DIR = Path("/home/vagrant/rust-benchmark-api")
EXPECTED_SCRIPT_PATH = PROJECT_DIR / "benchmarks/cold-start/cold_start.py"
SCRIPT_DIR = EXPECTED_SCRIPT_PATH.parent
CONDITIONING_DIR = SCRIPT_DIR / "conditioning"
PILOT_DIR = SCRIPT_DIR / "pilot"
OFFICIAL_DIR = SCRIPT_DIR / "results/official"
DISCARDED_DIR = SCRIPT_DIR / "results/discarded"
CAMPAIGN_DIR = SCRIPT_DIR / "results/campaigns"
LOCAL_BINARY = PROJECT_DIR / "target/release/rust-benchmark-api"
LOCAL_SHA256 = (
    "98743849fdbaa84be00ad75d6c680314"
    "8600dbffe74d0858e83944c59747b8b3"
)
DOCKER_IMAGE = "rust-benchmark-api:0.1.0-8503f41a"  # Referência histórica.
DOCKER_IMAGE_ID = (
    "sha256:"
    "1df15a150099d4d5258f2b68851debcf"
    "dd9e6295766a1f9ff76c347cab948015"
)
DOCKER_BINARY_SHA256 = (
    "8aeee772194e00fce4d1971206f98cd4"
    "fe3aadcdf0fad87977c05ede849ef194"
)
WASM_ARTIFACT = PROJECT_DIR / "target/wasm32-wasip2/release/rust_benchmark_api.wasm"
WASM_SHA256 = (
    "e168ee966b7e4c681687e2b3f3d2916e"
    "2d4defd6c836ffc1d258d6a577c3796d"
)
SPIN_TOML = PROJECT_DIR / "spin.toml"
SPIN_TOML_SHA256 = (
    "549c60ca843eb894b76995c705b65797"
    "9e11c03d9c6aeb9d261d4ee67821de09"
)
EXPECTED_SPIN_VERSION = "spin 4.1.0 (c0b3726 2026-08-25)"
DOCKER_SOCKET = "unix:///var/run/docker.sock"
CONTAINER_PREFIX = "rust-benchmark-api-coldstart-"
OWNER_LABEL = "org.tcc.cold-start.sample"
CONFIG = {
    "benchmark-local": {
        "environment": "local", "host": "192.168.56.10", "port": 3000,
        "artifact_sha256_expected": LOCAL_SHA256,
    },
    "benchmark-docker": {
        "environment": "docker", "host": "192.168.56.11", "port": 3000,
        "artifact_sha256_expected": DOCKER_BINARY_SHA256,
    },
    "benchmark-wasm": {
        "environment": "wasm", "host": "192.168.56.12", "port": 3000,
        "artifact_sha256_expected": WASM_SHA256,
    },
}
STOP_SIGNAL = None


class ProceduralError(RuntimeError):
    pass


class ProbeDeadline(TimeoutError):
    pass


class PublicationError(ProceduralError):
    def __init__(self, path, digest, published, detail):
        self.path = path
        self.digest = digest
        self.published = published
        super().__init__(
            f"Publicação de {path} falhou; destino_publicado={published}: {detail}"
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


HTTP = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), NoRedirect()
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def request_stop(signum, _frame):
    # Não levanta exceção entre Popen e o registro de posse, nem no cleanup.
    global STOP_SIGNAL
    STOP_SIGNAL = signum


def alarm_expired(_signum, _frame):
    raise ProbeDeadline("deadline absoluto do probe")


def check_stop():
    if STOP_SIGNAL is not None:
        raise ProceduralError(f"Interrupção solicitada pelo sinal {STOP_SIGNAL}.")


def sleep_interruptible(seconds):
    deadline = time.monotonic() + seconds
    while True:
        check_stop()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.100, remaining))


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_output_directory(path):
    missing = []
    directory = path
    while not directory.exists():
        missing.append(directory)
        directory = directory.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        fsync_directory(directory)
        fsync_directory(directory.parent)


def write_json_exclusive(path, data):
    """Publica JSON completo, estrito e sem substituir destino existente."""
    payload = (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True,
                   allow_nan=False) + "\n"
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    temporary = None
    published = False
    try:
        ensure_output_directory(path.parent)
        fd, name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)
        published = True
        fsync_directory(path.parent)
    except Exception as exc:
        raise PublicationError(path, digest, published, str(exc)) from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # O nome publicado já foi sincronizado; não o sobrescrever.
                pass
    return {"path": str(path), "sha256": digest, "publication_confirmed": True}


def output_path(mode, environment, repetition):
    root = PILOT_DIR if mode == "pilot" else OFFICIAL_DIR
    return root / environment / f"r{repetition:02d}.json"


def conditioning_path(mode, environment):
    return CONDITIONING_DIR / environment / f"{mode}.json"


def discarded_path(environment, label, sample_id):
    return DISCARDED_DIR / environment / f"{label}-{sample_id}.json"


def acquire_lock():
    fd = os.open(
        SCRIPT_DIR / ".cold_start.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
    )
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise ProceduralError("Outra campanha está ativa nesta VM.")
    return fd


def read_proc_stat(pid):
    try:
        text = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = text[text.rfind(")") + 1:].split()
    if len(fields) < 20:
        raise ProceduralError(f"/proc/{pid}/stat incompleto.")
    return {
        "pid": int(pid), "state": fields[0], "group": int(fields[2]),
        "session": int(fields[3]), "start_ticks": int(fields[19]),
    }


def session_members(session_id):
    members = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            stat = read_proc_stat(entry.name)
            if (stat is not None and stat["session"] == session_id
                    and stat["state"] not in ("Z", "X", "x")):
                members.append(stat)
    return members


def child_exit_code(process):
    """Observa a saída sem reap: o PID continua reservado até o cleanup."""
    info = os.waitid(
        os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT
    )
    if info is None:
        return None
    if info.si_code == os.CLD_EXITED:
        return info.si_status
    return -info.si_status


def signal_initial_group(process, sig):
    # O filho não foi recolhido: seu PID continua reservado a esta execução.
    leader = read_proc_stat(process.pid)
    if (leader is None or leader["session"] != process.pid
            or leader["group"] != process.pid):
        raise ProceduralError("Identidade da sessão criada não confirmada.")
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass  # A confirmação de ausência continua obrigatória.


def signal_owned_session(process, sig):
    # Sinaliza primeiro o grupo conhecido, mesmo se outra leitura de /proc falhar.
    signal_initial_group(process, sig)
    for group in {member["group"] for member in session_members(process.pid)}:
        if group != process.pid:
            try:
                os.killpg(group, sig)
            except (ProcessLookupError, PermissionError):
                pass


def stop_owned_process(process):
    """TERM/KILL apenas na sessão própria; inclui filhos de outros PGIDs."""
    forced = False
    errors = []
    try:
        signal_owned_session(process, signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while session_members(process.pid):
            if time.monotonic() >= deadline:
                forced = True
                signal_owned_session(process, signal.SIGKILL)
                break
            time.sleep(0.050)
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while session_members(process.pid):
            if time.monotonic() >= deadline:
                raise ProceduralError(
                    f"Sessão {process.pid} ainda tem processos; cleanup inconclusivo."
                )
            time.sleep(0.050)
    except Exception as exc:
        errors.append(str(exc))
        # Melhor esforço no grupo conhecido; cleanup permanece inconclusivo.
        try:
            forced = True
            signal_initial_group(process, signal.SIGKILL)
        except Exception as kill_exc:
            errors.append(str(kill_exc))
    try:
        returncode = process.wait(timeout=STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        returncode = None
        errors.append("Filho direto não pôde ser recolhido.")
    if errors:
        raise ProceduralError(" | ".join(errors))
    return {"forced": forced, "returncode": returncode}

def read_tail(file, limit=8192):
    file.flush()
    size = file.seek(0, os.SEEK_END)
    file.seek(max(0, size - limit))
    return file.read(limit).decode("utf-8", errors="replace")


def wait_child(process, seconds, interruptible):
    deadline = time.monotonic() + seconds
    while True:
        if interruptible:
            check_stop()
        code = child_exit_code(process)
        if code is not None:
            return code
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        time.sleep(min(0.020, remaining))


def docker_command(context, args, timeout=DOCKER_COMMAND_TIMEOUT_SECONDS,
                   interruptible=True):
    """Comando operacional fora da janela medida; captura em arquivos, não pipes."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        process = subprocess.Popen(
            context["docker_base"] + args, cwd=PROJECT_DIR,
            env=context["env"], stdin=subprocess.DEVNULL,
            stdout=out, stderr=err, start_new_session=True,
        )
        try:
            code = wait_child(process, timeout, interruptible)
        finally:
            cleanup = stop_owned_process(process)
        stdout = read_tail(out, 1024 * 1024)
        stderr = read_tail(err, 65536)
        if code is None:
            raise ProceduralError(
                f"Comando Docker excedeu {timeout}s: {args[0]}."
            )
        if cleanup["returncode"] != code:
            raise ProceduralError("Código de saída Docker inconsistente.")
        return code, stdout.strip(), stderr.strip()


def inspect_container(context, name_or_id):
    code, out, err = docker_command(
        context, ["container", "inspect", name_or_id], interruptible=False
    )
    if code != 0:
        if "No such object" in err or "No such container" in err:
            return None
        raise ProceduralError(f"docker inspect falhou: {err}")
    objects = json.loads(out)
    if not isinstance(objects, list) or len(objects) != 1:
        raise ProceduralError("docker inspect retornou estrutura inesperada.")
    return objects[0]


def assert_owned_container(obj, runtime):
    cid = obj.get("Id", "")
    labels = (obj.get("Config") or {}).get("Labels") or {}
    if (not re.fullmatch(r"[0-9a-f]{64}", cid)
            or obj.get("Name") != "/" + runtime["container_name"]
            or obj.get("Image") != DOCKER_IMAGE_ID
            or labels.get(OWNER_LABEL) != runtime["sample_id"]):
        raise ProceduralError(
            "Container não corresponde a nome + label + image ID esperados."
        )
    return cid


def cleanup_container(context, runtime, cli_code, cleanup):
    # O CLI pode falhar depois de o daemon criar um container.
    obj = inspect_container(context, runtime["container_name"])
    if obj is None and cli_code != 0:
        deadline = time.monotonic() + DOCKER_RECONCILE_SECONDS
        while obj is None and time.monotonic() < deadline:
            time.sleep(0.100)
            obj = inspect_container(context, runtime["container_name"])
    if obj is None:
        if cli_code == 0:
            raise ProceduralError("docker run retornou sucesso, mas não há container.")
        raise ProceduralError(
            "Criação Docker não confirmada. Pode haver criação tardia pelo daemon; "
            f"verificar manualmente o nome {runtime['container_name']}."
        )
    cid = assert_owned_container(obj, runtime)
    runtime["container_id"] = cid
    cleanup["container_id"] = cid
    identity_errors = []
    try:
        cidfile = runtime["temporary_dir"] / "container.cid"
        recorded = cidfile.read_text(encoding="ascii").strip() if cidfile.exists() else ""
        cleanup["cidfile_content"] = recorded[:256]
        if recorded and recorded != cid:
            identity_errors.append("CID file parcial ou divergente.")
        if cli_code == 0:
            if recorded != cid:
                identity_errors.append("Lançamento normal sem CID file íntegro.")
            if read_tail(runtime["stdout_file"]).strip() != cid:
                identity_errors.append("ID do stdout Docker diverge do inspect.")
    except (OSError, UnicodeError) as exc:
        identity_errors.append(f"Falha ao ler CID file: {exc}")
    # Nome + UUID label + .Image provam a posse. CID/stdout inconsistente
    # invalida a amostra, mas não impede remover este container próprio pelo ID.
    state = obj.get("State") or {}
    cleanup["container_status_before_cleanup"] = state.get("Status")
    cleanup["container_exit_code_before_cleanup"] = state.get("ExitCode")
    if state.get("Running"):
        try:
            code, _, err = docker_command(
                context, ["container", "stop", "--time",
                          str(int(STOP_TIMEOUT_SECONDS)), cid],
                timeout=STOP_TIMEOUT_SECONDS + 2.0, interruptible=False,
            )
        except Exception as exc:
            code, err = -1, str(exc)
            identity_errors.append(f"Comando stop inconclusivo: {exc}")
        if code != 0:
            current = inspect_container(context, cid)
            if current is None:
                raise ProceduralError("Container desapareceu durante o stop.")
            assert_owned_container(current, runtime)
            if (current.get("State") or {}).get("Running"):
                cleanup["forced"] = True
                code, _, err = docker_command(
                    context, ["container", "kill", cid], interruptible=False
                )
                if code != 0:
                    raise ProceduralError(f"Falha ao parar container próprio: {err}")
    # stop pode usar SIGKILL internamente e ainda retornar 0.
    stopped = inspect_container(context, cid)
    if stopped is None:
        raise ProceduralError("Container desapareceu antes da remoção confirmada.")
    assert_owned_container(stopped, runtime)
    stopped_state = stopped.get("State") or {}
    cleanup["container_exit_code_after_stop"] = stopped_state.get("ExitCode")
    if stopped_state.get("Running"):
        raise ProceduralError("Container continua em execução após stop/kill.")
    cleanup["forced_or_exit_137"] = (
        cleanup["forced"] or stopped_state.get("ExitCode") == 137
    )
    code, _, err = docker_command(
        context, ["container", "rm", cid], interruptible=False
    )
    if code != 0:
        raise ProceduralError(f"Falha ao remover container próprio: {err}")
    if (inspect_container(context, cid) is not None
            or inspect_container(context, runtime["container_name"]) is not None):
        raise ProceduralError("Remoção do container não confirmada.")
    cleanup["container_removal_confirmed"] = True
    if cli_code != 0:
        identity_errors.append("Container removido após lançamento Docker anormal.")
    if identity_errors:
        raise ProceduralError(" | ".join(identity_errors))


def port_is_open(config):
    try:
        with socket.create_connection(
            (config["host"], config["port"]), PROBE_TIMEOUT_SECONDS
        ):
            return True
    except OSError as exc:
        if exc.errno == errno.ECONNREFUSED:
            return False
        raise ProceduralError(f"Estado da porta inconclusivo: {exc}") from exc


def wait_port_closed(config):
    deadline = time.monotonic() + PORT_CLOSE_TIMEOUT_SECONDS
    while port_is_open(config):
        if time.monotonic() >= deadline:
            raise ProceduralError("A porta 3000 permaneceu aberta após o cleanup.")
        time.sleep(PROBE_INTERVAL_SECONDS)


def assert_no_previous_runtime(context):
    config = context["config"]
    if port_is_open(config):
        raise ProceduralError("Porta 3000 já está respondendo.")
    # Confirma endereço atribuído à VM e ausência de listener local.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((config["host"], config["port"]))
    environment = config["environment"]
    if environment == "docker":
        code, out, err = docker_command(
            context, ["container", "ls", "--all", "--quiet", "--no-trunc",
                      "--filter", f"name={CONTAINER_PREFIX}"]
        )
        if code != 0:
            raise ProceduralError(f"Não foi possível buscar resíduos Docker: {err}")
        if out:
            raise ProceduralError(
                "Há containers anteriores do harness (inclusive parados): " + out
            )
        return
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as exc:
            raise ProceduralError("Não foi possível verificar processos.") from exc
        args = [part.decode("utf-8", errors="replace")
                for part in raw.split(b"\0") if part]
        if not args:
            continue
        executable = Path(args[0]).name
        spin_names = {"spin", Path(context.get("spin_path", "spin")).name}
        if (environment == "local" and executable == LOCAL_BINARY.name
                or environment == "wasm" and executable in spin_names
                and "up" in args[1:]):
            raise ProceduralError(f"Runtime pré-existente encontrado: PID {entry.name}.")


def verify_file(path, expected, executable=False):
    if not path.is_file() or executable and not os.access(path, os.X_OK):
        raise ProceduralError(f"Artefato ausente ou não executável: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise ProceduralError(f"SHA-256 não confere: {path}")
    return actual


def executable_path(name, env):
    resolved = shutil.which(name, path=env["PATH"])
    if resolved is None:
        raise ProceduralError(f"Executável ausente: {name}")
    return str(Path(resolved).resolve())


def verify_preflight(mode, repetitions):
    check_stop()
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise ProceduralError(f"Python esperado: {EXPECTED_PYTHON}; atual: {sys.version}.")
    if sys.platform != "linux" or os.geteuid() == 0:
        raise ProceduralError("Use Linux e o usuário normal da VM, sem sudo no harness.")
    source = Path(__file__).resolve()
    if source != EXPECTED_SCRIPT_PATH:
        raise ProceduralError(f"Instale o arquivo em {EXPECTED_SCRIPT_PATH}.")
    hostname = socket.gethostname()
    if hostname not in CONFIG:
        raise ProceduralError(f"Hostname não reconhecido: {hostname}.")
    sidecar = source.with_name(source.name + ".sha256")
    parts = sidecar.read_text(encoding="ascii").split()
    if (len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0])
            or parts[1] != source.name):
        raise ProceduralError("Sidecar SHA-256 inválido.")
    source_hash = verify_file(source, parts[0])
    account = pwd.getpwuid(os.getuid())
    env = {
        "HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
        "PATH": ("/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:"
                 + account.pw_dir + "/.spin/bin"),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
    }
    config = dict(CONFIG[hostname])
    config["url"] = f"http://{config['host']}:{config['port']}/health"
    context = {
        "config": config, "env": env, "source_hash": source_hash,
        "artifact_sha256_observed": None, "provenance": {},
    }
    environment = config["environment"]
    if environment == "local":
        context["artifact_sha256_observed"] = verify_file(
            LOCAL_BINARY, LOCAL_SHA256, executable=True
        )
    elif environment == "wasm":
        context["artifact_sha256_observed"] = verify_file(WASM_ARTIFACT, WASM_SHA256)
        context["provenance"]["spin_toml_sha256"] = verify_file(
            SPIN_TOML, SPIN_TOML_SHA256
        )
        spin = executable_path("spin", env)
        context["spin_path"] = spin
        context["provenance"]["spin_executable_sha256"] = sha256_file(spin)
        # Wrapper operacional sem shell; a mesma versão absoluta será lançada.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(
                [spin, "--version"], env=env, cwd=PROJECT_DIR,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                start_new_session=True,
            )
            try:
                code = wait_child(process, 5.0, True)
            finally:
                stop_owned_process(process)
            version = read_tail(out).strip()
            if code != 0 or version != EXPECTED_SPIN_VERSION:
                raise ProceduralError(f"Versão inesperada do Spin: {version}")
        context["provenance"]["spin_version"] = version
    else:
        sudo = executable_path("sudo", env)
        docker = executable_path("docker", env)
        context["docker_base"] = [sudo, "-n", "--", docker, "--host", DOCKER_SOCKET]
        code, out, err = docker_command(
            context, ["image", "inspect", DOCKER_IMAGE_ID]
        )
        if code != 0:
            raise ProceduralError(f"Imagem imutável não disponível: {err}")
        images = json.loads(out)
        if len(images) != 1 or images[0].get("Id") != DOCKER_IMAGE_ID:
            raise ProceduralError("Image ID observado não confere.")
        image = images[0]
        image_config = image.get("Config") or {}
        context["provenance"]["docker_image"] = {
            "id": image["Id"], "os": image.get("Os"),
            "architecture": image.get("Architecture"),
            "entrypoint": image_config.get("Entrypoint"),
            "cmd": image_config.get("Cmd"),
            "env": image_config.get("Env"),
            "user": image_config.get("User"),
        }
        code, out, err = docker_command(
            context, ["version", "--format", "{{json .}}"]
        )
        if code != 0:
            raise ProceduralError(f"Não foi possível obter versão Docker: {err}")
        context["provenance"]["docker_version"] = json.loads(out)
        # O hash do binário interno é uma referência, não uma verificação feita aqui.
        context["provenance"]["docker_binary_hash_verified"] = False
    planned = [conditioning_path(mode, environment)] + [
        output_path(mode, environment, i) for i in range(1, repetitions + 1)
    ]
    for path in planned:
        if os.path.lexists(path):
            raise ProceduralError(f"Resultado já existe; não será sobrescrito: {path}")
    assert_no_previous_runtime(context)
    return context


def build_runtime(context, label, sample_id):
    config = context["config"]
    environment = config["environment"]
    runtime = {
        "kind": "docker" if environment == "docker" else "process",
        "process": None, "sample_id": sample_id, "container_id": None,
        "temporary_dir": None, "stdout_file": None, "stderr_file": None,
    }
    if environment == "docker":
        name = CONTAINER_PREFIX + label + "-" + sample_id
        runtime["container_name"] = name
        directory = Path(tempfile.mkdtemp(prefix="tcc-cold-start-"))
        runtime["temporary_dir"] = directory
        runtime["command"] = context["docker_base"] + [
            "run", "--detach", "--name", name,
            "--label", f"{OWNER_LABEL}={sample_id}",
            "--cidfile", str(directory / "container.cid"),
            "--pull=never", "--restart=no", "--read-only", "--cap-drop=ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit=128",
            "--network", "bridge", "--publish", "192.168.56.11:3000:3000",
            DOCKER_IMAGE_ID,
        ]
        runtime["environment_updates"] = {}
    elif environment == "local":
        runtime["command"] = [str(LOCAL_BINARY)]
        runtime["environment_updates"] = {
            "RUST_BENCHMARK_ADDRESS": "192.168.56.10:3000"
        }
    else:
        runtime["command"] = [
            context["spin_path"], "up", "--from", str(SPIN_TOML),
            "--listen", "192.168.56.12:3000",
        ]
        runtime["environment_updates"] = {}
    return runtime


def probe_once(url, deadline_ns):
    start_ns = time.monotonic_ns()
    status = None
    content_type = None
    body = b""
    error = None
    timeout = max(0.0, (deadline_ns - start_ns) / 1_000_000_000)
    try:
        if timeout <= 0:
            raise ProbeDeadline("sem orçamento para o probe")
        signal.setitimer(signal.ITIMER_REAL, timeout)
        request = urllib.request.Request(
            url, method="GET",
            headers={"Accept": "application/json", "Connection": "close"},
        )
        try:
            response = HTTP.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.status
            content_type = response.headers.get("Content-Type")
            # Um byte extra detecta corpo maior que o contrato.
            body = response.read(len(EXPECTED_BODY) + 1)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
    end_ns = time.monotonic_ns()
    content_type_valid = (
        content_type is not None
        and content_type.split(";", 1)[0].strip().lower() == "application/json"
    )
    return {
        "start_ns": start_ns, "end_ns": end_ns,
        "duration_ns": end_ns - start_ns,
        "deadline_ns": deadline_ns, "timeout_ns": max(0, deadline_ns - start_ns),
        "deadline_exceeded": end_ns >= deadline_ns,
        "status": status, "content_type": content_type,
        "body_hex": body.hex(), "body": body.decode("utf-8", errors="replace"),
        "error": error,
        "valid": (error is None and status == EXPECTED_HTTP_STATUS
                  and body == EXPECTED_BODY and content_type_valid
                  and end_ns < deadline_ns),
    }


def validate_result(result):
    probes = result["probe_attempts"]
    if result["probe_count"] != len(probes):
        raise ProceduralError("Contagem de probes inconsistente.")
    if result["sample_valid"] and (
            result["procedural_error"] is not None
            or not result["cleanup"]["complete"]
            or not result["cleanup"]["port_closed"]):
        raise ProceduralError("Amostra válida sem cleanup confirmado.")
    start_ns = result["monotonic_start_ns"]
    previous_end_ns = start_ns
    for index, probe in enumerate(probes, 1):
        if (probe["index"] != index or start_ns is None
                or probe["start_ns"] < previous_end_ns
                or probe["end_ns"] < probe["start_ns"]
                or probe["duration_ns"] != probe["end_ns"] - probe["start_ns"]):
            raise ProceduralError("Tempos/índices de probes inconsistentes.")
        previous_end_ns = probe["end_ns"]
    if result["success"]:
        if (not probes or not probes[-1]["valid"]
                or result["monotonic_ready_ns"] != probes[-1]["end_ns"]
                or result["cold_start_ns"] != result["monotonic_ready_ns"] - start_ns
                or result["cold_start_ms"] != result["cold_start_ns"] / 1_000_000
                or not 0 <= result["cold_start_ns"] < int(
                    STARTUP_TIMEOUT_SECONDS * 1_000_000_000
                )):
            raise ProceduralError("Resultado de sucesso inconsistente.")
    elif result["cold_start_ns"] is not None or result["cold_start_ms"] is not None:
        raise ProceduralError("Falha não deve conter tempo de cold start.")
    # json.dumps(allow_nan=False) também valida todos os números aninhados.
    for key in ("cold_start_ms", "elapsed_ms", "max_probe_start_gap_ms",
                "max_probe_duration_ms"):
        value = result[key]
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ProceduralError(f"Valor inválido em {key}.")


def cleanup_runtime(context, runtime, cleanup):
    process = runtime["process"] if runtime is not None else None
    if process is None:
        cleanup["complete"] = True
        return
    errors = []
    cli_code = None
    try:
        if runtime["kind"] == "docker":
            # HTTP pode responder antes de docker run -d sair. Espera operacional
            # fora da janela medida, mantendo o PID reservado para o cleanup.
            cli_code = wait_child(process, STARTUP_TIMEOUT_SECONDS, False)
        else:
            cli_code = child_exit_code(process)
    except Exception as exc:
        errors.append(f"Estado do CLI/processo: {exc}")
    try:
        process_cleanup = stop_owned_process(process)
        cleanup["forced"] = process_cleanup["forced"]
        cleanup["runtime_returncode"] = process_cleanup["returncode"]
    except Exception as exc:
        errors.append(f"Sessão do CLI/processo: {exc}")
    if runtime["kind"] == "docker":
        cleanup["docker_cli_exit_before_cleanup"] = cli_code
        try:
            cleanup_container(context, runtime, cli_code, cleanup)
        except Exception as exc:
            errors.append(f"Container: {exc}")
    if errors:
        raise ProceduralError(" | ".join(errors))
    cleanup["complete"] = True


def finalize_runtime_files(runtime, cleanup):
    if runtime is None:
        return {}
    logs = {}
    errors = []
    for name in ("stdout_file", "stderr_file"):
        file = runtime[name]
        if file is not None:
            try:
                logs[name.replace("_file", "_tail")] = read_tail(file)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
            finally:
                try:
                    file.close()
                except OSError as exc:
                    errors.append(f"Fechamento de {name}: {exc}")
                runtime[name] = None
    directory = runtime["temporary_dir"]
    if directory is not None:
        if cleanup["complete"]:
            try:
                # Não usa remoção recursiva nem apaga diretórios externos.
                (directory / "container.cid").unlink(missing_ok=True)
                directory.rmdir()
            except OSError as exc:
                logs["preserved_cid_directory"] = str(directory)
                errors.append(f"Temporários: {exc}")
        else:
            logs["preserved_cid_directory"] = str(directory)
    if errors:
        logs["collection_errors"] = errors
    return logs


def run_one(context, phase, repetition, label, destination, campaign, records):
    config = context["config"]
    sample_id = uuid.uuid4().hex
    result = {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": campaign["campaign_id"], "sample_id": sample_id,
        "environment": config["environment"], "hostname": socket.gethostname(),
        "phase": phase, "mode": campaign["mode"], "repetition": repetition,
        "label": label, "url": config["url"],
        "measurement_definition": campaign["measurement_definition"],
        "wall_timestamp_start": None, "wall_timestamp_ready": None,
        "wall_timestamp_end": None, "monotonic_start_ns": None,
        "monotonic_ready_ns": None, "monotonic_measurement_end_ns": None,
        "cold_start_ns": None, "cold_start_ms": None, "elapsed_ms": None,
        "probe_count": 0, "max_probe_start_gap_ms": None,
        "max_probe_duration_ms": None, "probe_attempts": [],
        "http_status": None, "response_body": None, "content_type": None,
        "success": False, "sample_valid": False, "failure_reason": None,
        "procedural_error": None, "runtime_command": None,
        "runtime_environment": None, "runtime_identifier": None,
        "runtime_returncode_before_cleanup": None,
        "artifact_sha256_expected": config["artifact_sha256_expected"],
        "artifact_sha256_observed": context["artifact_sha256_observed"],
        "docker_image_id": DOCKER_IMAGE_ID if config["environment"] == "docker" else None,
        "harness_sha256": context["source_hash"],
        "python_version": sys.version.split()[0],
        "startup_timeout_ms": STARTUP_TIMEOUT_SECONDS * 1000,
        "probe_timeout_ms": PROBE_TIMEOUT_SECONDS * 1000,
        "probe_interval_ms": PROBE_INTERVAL_SECONDS * 1000,
        "probe_interval_definition": "sono após probe inválido; não é frequência fixa",
        "cooldown_seconds": COOLDOWN_SECONDS,
        "stabilization_seconds": STABILIZATION_SECONDS,
        "cleanup": {
            "complete": False, "forced": False, "runtime_returncode": None,
            "container_id": None, "container_removal_confirmed": False,
            "port_closed": False,
        },
    }
    runtime = None
    errors = []
    measurement_started = False
    try:
        check_stop()
        assert_no_previous_runtime(context)
        if sha256_file(EXPECTED_SCRIPT_PATH) != context["source_hash"]:
            raise ProceduralError("Harness foi alterado durante a campanha.")
        runtime = build_runtime(context, label, sample_id)
        # Toda preparação dos arquivos ocorre antes de t0.
        runtime["stdout_file"] = tempfile.TemporaryFile()
        runtime["stderr_file"] = tempfile.TemporaryFile()
        env = dict(context["env"])
        env.update(runtime["environment_updates"])
        result["runtime_command"] = runtime["command"]
        result["runtime_environment"] = env
        result["wall_timestamp_start"] = utc_now()
        start_ns = time.monotonic_ns()
        result["monotonic_start_ns"] = start_ns
        measurement_started = True
        runtime["process"] = subprocess.Popen(
            runtime["command"], cwd=PROJECT_DIR, env=env,
            stdin=subprocess.DEVNULL, stdout=runtime["stdout_file"],
            stderr=runtime["stderr_file"], start_new_session=True,
        )
        result["runtime_identifier"] = runtime["process"].pid
        if runtime["kind"] == "docker":
            result["container_name"] = runtime["container_name"]
        deadline_ns = start_ns + int(STARTUP_TIMEOUT_SECONDS * 1_000_000_000)
        previous_start_ns = None
        while True:
            check_stop()
            now_ns = time.monotonic_ns()
            if now_ns >= deadline_ns:
                result["failure_reason"] = "startup_timeout"
                break
            code = child_exit_code(runtime["process"])
            if code is not None:
                result["runtime_returncode_before_cleanup"] = code
                if runtime["kind"] == "process":
                    result["failure_reason"] = "runtime_exited_before_ready"
                    break
                if code != 0:
                    raise ProceduralError(f"docker run falhou (código {code}).")
            probe_deadline = min(
                deadline_ns,
                time.monotonic_ns() + int(PROBE_TIMEOUT_SECONDS * 1_000_000_000),
            )
            probe = probe_once(config["url"], probe_deadline)
            probe["index"] = len(result["probe_attempts"]) + 1
            probe["start_offset_ns"] = probe["start_ns"] - start_ns
            probe["start_gap_ns"] = (
                None if previous_start_ns is None
                else probe["start_ns"] - previous_start_ns
            )
            previous_start_ns = probe["start_ns"]
            result["probe_attempts"].append(probe)
            if probe["valid"] and probe["end_ns"] < deadline_ns:
                result["success"] = True
                result["monotonic_ready_ns"] = probe["end_ns"]
                result["cold_start_ns"] = probe["end_ns"] - start_ns
                result["cold_start_ms"] = result["cold_start_ns"] / 1_000_000
                result["wall_timestamp_ready"] = utc_now()
                break
            now_ns = time.monotonic_ns()
            if now_ns >= deadline_ns:
                result["failure_reason"] = "startup_timeout"
                break
            time.sleep(min(
                PROBE_INTERVAL_SECONDS, (deadline_ns - now_ns) / 1_000_000_000
            ))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        # Preserva métricas parciais inclusive em interrupções/erros procedurais.
        if measurement_started:
            end_ns = time.monotonic_ns()
            result["monotonic_measurement_end_ns"] = end_ns
            result["elapsed_ms"] = (end_ns - result["monotonic_start_ns"]) / 1_000_000
        result["wall_timestamp_end"] = utc_now()
        probes = result["probe_attempts"]
        result["probe_count"] = len(probes)
        if probes:
            result["max_probe_duration_ms"] = max(
                probe["duration_ns"] for probe in probes
            ) / 1_000_000
            result["http_status"] = probes[-1]["status"]
            result["response_body"] = probes[-1]["body"]
            result["content_type"] = probes[-1]["content_type"]
        if len(probes) > 1:
            result["max_probe_start_gap_ms"] = max(
                probe["start_gap_ns"] for probe in probes[1:]
            ) / 1_000_000
        try:
            cleanup_runtime(context, runtime, result["cleanup"])
        except Exception as exc:
            errors.append(f"Cleanup: {type(exc).__name__}: {exc}")
        try:
            wait_port_closed(config)
            result["cleanup"]["port_closed"] = True
        except Exception as exc:
            errors.append(f"Porta: {type(exc).__name__}: {exc}")
        try:
            result["runtime_logs"] = finalize_runtime_files(runtime, result["cleanup"])
            errors.extend(result["runtime_logs"].get("collection_errors", []))
        except Exception as exc:
            errors.append(f"Logs/temporários: {type(exc).__name__}: {exc}")
        if runtime is not None and runtime.get("container_id") is not None:
            result["runtime_identifier"] = runtime["container_id"]
        if STOP_SIGNAL is not None:
            errors.append(f"Interrupção solicitada pelo sinal {STOP_SIGNAL}.")
    if not result["success"] and not result["failure_reason"]:
        result["failure_reason"] = "procedural_error"
    result["sample_valid"] = not errors
    result["procedural_error"] = " | ".join(errors) if errors else None
    try:
        validate_result(result)
    except Exception as exc:
        errors.append(f"Validação do JSON: {exc}")
        result["sample_valid"] = False
        result["procedural_error"] = " | ".join(errors)
    target = (
        discarded_path(config["environment"], label, sample_id) if errors else destination
    )
    try:
        record = write_json_exclusive(target, result)
    except PublicationError as exc:
        records.append({
            "path": str(exc.path), "sha256": exc.digest,
            "publication_confirmed": False, "destination_published": exc.published,
            "sample_valid": False, "publication_error": str(exc),
            "result_snapshot": result,
        })
        raise
    record.update({
        "sample_id": sample_id, "phase": phase,
        "sample_valid": result["sample_valid"], "success": result["success"],
    })
    records.append(record)
    if errors:
        raise ProceduralError(result["procedural_error"])
    return result


def host_metadata():
    return {
        "hostname": socket.gethostname(), "uname": list(platform.uname()),
        "python": sys.version, "uid": os.getuid(), "cpu_count": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "os_release": Path("/etc/os-release").read_text(encoding="utf-8"),
        "loadavg_before_stabilization": Path("/proc/loadavg").read_text().strip(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["pilot", "official"])
    args = parser.parse_args()
    repetitions = PILOT_REPETITIONS if args.mode == "pilot" else OFFICIAL_REPETITIONS
    lock_fd = None
    previous_handlers = {}
    context = None
    records = []
    campaign = {
        "schema_version": SCHEMA_VERSION, "campaign_id": uuid.uuid4().hex,
        "mode": args.mode, "expected_repetitions": repetitions,
        "started_at": utc_now(), "finished_at": None, "complete": False,
        "error": None, "files": records,
        "measurement_definition": (
            "lançamento de processo/runtime até resposta HTTP completa e válida; "
            "host e caches aquecidos por uma execução de condicionamento; "
            "Docker inclui sudo, CLI, criação e publicação de porta"
        ),
    }
    exit_code = 1
    try:
        if sys.platform != "linux":
            raise ProceduralError("Este harness exige Linux.")
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise ProceduralError("Já existe um temporizador SIGALRM ativo.")
        for signum, handler in (
            (signal.SIGINT, request_stop), (signal.SIGTERM, request_stop),
            (signal.SIGALRM, alarm_expired),
        ):
            previous_handlers[signum] = signal.signal(signum, handler)
        lock_fd = acquire_lock()
        context = verify_preflight(args.mode, repetitions)
        campaign.update({
            "environment": context["config"]["environment"],
            "harness_sha256": context["source_hash"],
            "host": host_metadata(), "runtime_provenance": context["provenance"],
        })
        print(f"Preflight concluído. SHA-256: {context['source_hash']}", flush=True)
        print("Estabilização: 60 segundos.", flush=True)
        sleep_interruptible(STABILIZATION_SECONDS)
        environment = context["config"]["environment"]
        conditioning = run_one(
            context, "conditioning", None, f"conditioning-{args.mode}",
            conditioning_path(args.mode, environment), campaign, records,
        )
        if not conditioning["success"]:
            raise ProceduralError("Condicionamento sem resposta HTTP válida.")
        sleep_interruptible(COOLDOWN_SECONDS)
        failures = 0
        for repetition in range(1, repetitions + 1):
            label = f"{args.mode}-r{repetition:02d}"
            print(f"Iniciando {label}...", flush=True)
            result = run_one(
                context, args.mode, repetition, label,
                output_path(args.mode, environment, repetition), campaign, records,
            )
            if result["success"]:
                print(f"{label}: {result['cold_start_ms']:.3f} ms", flush=True)
            else:
                failures += 1
                print(f"{label}: falha real ({result['failure_reason']})", flush=True)
            if repetition < repetitions:
                sleep_interruptible(COOLDOWN_SECONDS)
        check_stop()
        campaign["complete"] = True
        campaign["real_failures"] = failures
        exit_code = 2 if failures else 0
    except Exception as exc:
        campaign["error"] = f"{type(exc).__name__}: {exc}"
        print(f"ABORTADO: {campaign['error']}", file=sys.stderr, flush=True)
    finally:
        campaign["finished_at"] = utc_now()
        campaign["stop_signal"] = STOP_SIGNAL
        if context is not None:
            manifest_path = (
                CAMPAIGN_DIR / context["config"]["environment"]
                / f"{args.mode}-{campaign['campaign_id']}.json"
            )
            try:
                write_json_exclusive(manifest_path, campaign)
                print(f"Manifesto da campanha: {manifest_path}", flush=True)
            except Exception as exc:
                exit_code = 1
                print(f"Falha ao publicar manifesto: {exc}", file=sys.stderr, flush=True)
        if lock_fd is not None:
            os.close(lock_fd)
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    if exit_code == 0:
        print("Campanha concluída; todas as medições responderam corretamente.")
    elif exit_code == 2:
        print("Campanha concluída com falhas reais registradas.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
