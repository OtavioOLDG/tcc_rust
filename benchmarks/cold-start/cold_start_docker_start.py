#!/usr/bin/env python3
"""Partida HTTP com docker start: um contêiner reutilizado por campanha.

Derivado de cold_start.py, SHA-256 95d135af7164f0e4c36bd412ca3ccc4e12846b9be70034b7bdbc86c7b9c8bdcc.
Linux/Python 3.10.12, somente VM benchmark-docker, usuário normal.
Uma criação fora da janela; primeira partida no condicionamento; partidas
posteriores medidas no mesmo contêiner parado. CLI/sudo/rede de partida
permanecem dentro da medida. Não mede caches frios nem somente o runtime.
Autossuficiente: requer apenas este arquivo, Python padrão e Docker já instalado.
Não inicia campanha ao importar. --help não acessa Docker.

Na VM, salvar em benchmarks/cold-start/cold_start_docker_start.py, dentro
do projeto original. Não substituir cold_start.py. Não requer sidecar .sha256.
Uso: python3 cold_start_docker_start.py pilot     (2 tentativas + condicionamento)
     python3 cold_start_docker_start.py official  (30 tentativas + condicionamento)
Executar como usuário normal; o acesso ao daemon usa sudo -n, como no original.
Cada campanha recebe um UUID em docker-start-reused/<modo>/<UUID>/, ao lado
deste script: conditioning.json, samples/rNN.json, descartes e manifest.json.
Nenhum resultado anterior é sobrescrito. Ao final (inclusive em interrupções),
tenta remover exclusivamente o contêiner próprio, confirmado por ID e labels.
Saída 0: completa sem falhas; 2: completa com falhas reais; 1: erro procedural.
Timeouts válidos permanecem na série; erro procedural interrompe a campanha.
--expected-sha256 HASH é opcional para conferir a cópia antes da preparação.
Versão v2: limpeza conservadora com prazos separados e diagnóstico estruturado.
Docker stop recebe 5 s de tolerância graciosa; o CLI pode aguardar até 15 s.
Timeout do comando continua sendo erro procedural, mesmo com parada confirmada.
Uma nova versão exige novo piloto antes de uma nova campanha oficial.
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


EXPECTED_PYTHON = (3, 10, 12)
OFFICIAL_REPETITIONS = 30
PILOT_REPETITIONS = 2
STABILIZATION_SECONDS = 60.0
STARTUP_TIMEOUT_SECONDS = 10.0
PROBE_TIMEOUT_SECONDS = 0.200
PROBE_INTERVAL_SECONDS = 0.010
COOLDOWN_SECONDS = 5.0
CONTAINER_STOP_GRACE_SECONDS = 5.0
DOCKER_STOP_COMMAND_TIMEOUT_SECONDS = 15.0
AUX_PROCESS_TIMEOUT_SECONDS = 5.0
CONTAINER_STATE_CONFIRM_TIMEOUT_SECONDS = 3.0
PORT_CLOSE_TIMEOUT_SECONDS = 3.0
DOCKER_COMMAND_TIMEOUT_SECONDS = 5.0
DOCKER_RECONCILE_SECONDS = 3.0
EXPECTED_HTTP_STATUS = 200
EXPECTED_BODY = b'{"status":"ok"}'
SCHEMA_VERSION = 4
HARNESS_VERSION = "docker-start-reused-v2"
PARENT_HARNESS_SHA256 = "ebc2200a70ca54d7e9f13748f063f724d3d3cce546494fce672dc4c7e922fec0"
BASELINE_HARNESS_SHA256 = "95d135af7164f0e4c36bd412ca3ccc4e12846b9be70034b7bdbc86c7b9c8bdcc"
EXPERIMENT = "docker-start-reused-container"
MEASUREMENT_DEFINITION = (
    "invocação sudo + docker start de contêiner previamente criado e parado "
    "até resposta HTTP completa e válida; mesmo contêiner reutilizado na campanha; "
    "primeira partida no condicionamento; criação e limpeza fora da janela"
)
PROJECT_DIR = Path("/home/vagrant/rust-benchmark-api")
EXPECTED_SCRIPT_PATH = PROJECT_DIR / "benchmarks/cold-start/cold_start_docker_start.py"
SCRIPT_DIR = EXPECTED_SCRIPT_PATH.parent
DOCKER_IMAGE_ID = "sha256:1df15a150099d4d5258f2b68851debcfdd9e6295766a1f9ff76c347cab948015"
DOCKER_BINARY_SHA256 = "8aeee772194e00fce4d1971206f98cd4fe3aadcdf0fad87977c05ede849ef194"
DOCKER_SOCKET = "unix:///var/run/docker.sock"
# Conserva endereço, bridge e publicação do protocolo histórico para a comparação.
CONFIG = {"environment": "docker", "host": "192.168.56.11", "port": 3000,
          "artifact_sha256_expected": DOCKER_BINARY_SHA256,
          "url": "http://192.168.56.11:3000/health"}
CONTAINER_PREFIX = "rust-benchmark-api-coldstart-start-reused-"
RESIDUE_PREFIX = "rust-benchmark-api-coldstart-"
OWNER_LABEL = "org.tcc.cold-start.campaign"
EXPERIMENT_LABEL = "org.tcc.cold-start.experiment"
STOP_SIGNAL = None
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())


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
        pass

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
        deadline = time.monotonic() + AUX_PROCESS_TIMEOUT_SECONDS
        while session_members(process.pid):
            if time.monotonic() >= deadline:
                forced = True
                signal_owned_session(process, signal.SIGKILL)
                break
            time.sleep(0.050)
        deadline = time.monotonic() + AUX_PROCESS_TIMEOUT_SECONDS
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
        returncode = process.wait(timeout=AUX_PROCESS_TIMEOUT_SECONDS)
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
                   interruptible=True, observation=None):
    """Operação fora da medida. O prazo limita a espera, não todo o cleanup do CLI."""
    details = observation if observation is not None else {}
    started_ns = time.monotonic_ns()
    details.update({
        "command": context["docker_base"] + args, "operation": " ".join(args[:2]),
        "timeout_seconds": timeout, "wall_started_at": utc_now(),
        "monotonic_start_ns": started_ns, "wait_status": "not_started",
        "outcome": "not_started", "process_cleanup_complete": False,
        "cli_returncode": None, "cli_forced": None,
    })
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.Popen(
                context["docker_base"] + args, cwd=PROJECT_DIR,
                env=context["env"], stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, start_new_session=True,
            )
            details["cli_pid"] = process.pid
            details["wait_status"] = "waiting"
            code = None
            pending = None
            errors = []
            try:
                code = wait_child(process, timeout, interruptible)
                details["wait_status"] = "timeout" if code is None else "exited"
                details["outcome"] = ("timeout" if code is None else
                                      "success" if code == 0 else "nonzero_exit")
            except BaseException as exc:
                pending = exc
                details["wait_status"] = "error"
                details["outcome"] = "wait_error"
            finally:
                details["wait_end_ns"] = time.monotonic_ns()
                details["wait_duration_ms"] = (details["wait_end_ns"] - started_ns) / 1_000_000
                try:
                    cleanup = stop_owned_process(process)
                    details["cli_returncode"] = cleanup["returncode"]
                    details["cli_forced"] = cleanup["forced"]
                    details["process_cleanup_complete"] = cleanup["returncode"] is not None
                    if not details["process_cleanup_complete"]:
                        errors.append("CLI sem código de saída confirmado.")
                except Exception as exc:
                    details["process_cleanup_error"] = f"{type(exc).__name__}: {exc}"
                    errors.append(f"Encerramento do CLI: {exc}")
            if pending is not None:
                if not isinstance(pending, Exception):
                    raise pending
                errors.insert(0, f"Espera do CLI: {type(pending).__name__}: {pending}")
            elif code is None:
                errors.insert(0, f"Comando Docker excedeu {timeout}s: {details['operation']}.")
            elif details["cli_returncode"] is not None and details["cli_returncode"] != code:
                errors.append("Código de saída Docker inconsistente.")
            if errors:
                raise ProceduralError(" | ".join(errors))
            return code, read_tail(out, 1024 * 1024).strip(), read_tail(err, 65536).strip()
        except BaseException as exc:
            if details["wait_status"] == "not_started":
                details["outcome"] = "launch_error"
            details["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            ended_ns = time.monotonic_ns()
            details["monotonic_end_ns"] = ended_ns
            details["duration_including_cli_cleanup_ms"] = (ended_ns - started_ns) / 1_000_000
            details["wall_finished_at"] = utc_now()
            for key, file, limit in (("stdout_tail", out, 8192), ("stderr_tail", err, 65536)):
                try:
                    details[key] = read_tail(file, limit).strip()
                except Exception as exc:
                    details[key + "_error"] = f"{type(exc).__name__}: {exc}"


def inspect_container(context, name_or_id, timeout=DOCKER_COMMAND_TIMEOUT_SECONDS):
    code, out, err = docker_command(
        context, ["container", "inspect", name_or_id], timeout=timeout, interruptible=False
    )
    if code != 0:
        if "No such object" in err or "No such container" in err:
            return None
        raise ProceduralError(f"docker inspect falhou: {err}")
    objects = json.loads(out)
    if not isinstance(objects, list) or len(objects) != 1:
        raise ProceduralError("docker inspect retornou estrutura inesperada.")
    return objects[0]

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

def executable_path(name, env):
    resolved = shutil.which(name, path=env["PATH"])
    if resolved is None:
        raise ProceduralError(f"Executável ausente: {name}")
    return str(Path(resolved).resolve())

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

def host_metadata():
    return {
        "hostname": socket.gethostname(), "uname": list(platform.uname()),
        "python": sys.version, "uid": os.getuid(), "cpu_count": os.cpu_count(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "os_release": Path("/etc/os-release").read_text(encoding="utf-8"),
        "loadavg_before_stabilization": Path("/proc/loadavg").read_text().strip(),
    }

def validate_timing_result(result):
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

def assert_owned_container(obj, context):
    cid = obj.get("Id", "")
    labels = (obj.get("Config") or {}).get("Labels") or {}
    expected = context.get("container_id")
    if (not re.fullmatch(r"[0-9a-f]{64}", cid)
            or (expected is not None and cid != expected)
            or obj.get("Name") != "/" + context["container_name"]
            or obj.get("Image") != DOCKER_IMAGE_ID
            or labels.get(OWNER_LABEL) != context["campaign_id"]
            or labels.get(EXPERIMENT_LABEL) != EXPERIMENT):
        raise ProceduralError("Posse do contêiner não confirmada por ID/nome/labels/imagem.")
    return cid


def assert_environment_idle(context, allow_owned=False):
    config = context["config"]
    if port_is_open(config):
        raise ProceduralError("Porta de medição já está aberta.")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((config["host"], config["port"]))
    code, out, err = docker_command(context, [
        "container", "ls", "--all", "--quiet", "--no-trunc",
        "--filter", f"name={RESIDUE_PREFIX}",
    ])
    if code != 0:
        raise ProceduralError(f"Não foi possível procurar resíduos: {err}")
    allowed = {context["container_id"]} if allow_owned else set()
    if set(out.split()) - allowed:
        raise ProceduralError("Há contêineres de outra campanha; nenhuma remoção automática.")
    if allow_owned:
        obj = inspect_container(context, context["container_id"])
        if obj is None:
            raise ProceduralError("O contêiner da campanha desapareceu.")
        assert_owned_container(obj, context)
        state = obj.get("State") or {}
        expected_status = "created" if context["start_attempts"] == 0 else "exited"
        if (state.get("Status") != expected_status or state.get("Running")
                or state.get("Paused") or state.get("Restarting") or state.get("Pid") != 0):
            raise ProceduralError("Contêiner não está parado no estado esperado.")
        return {key: state.get(key) for key in
                ("Status", "Running", "Pid", "StartedAt", "FinishedAt", "ExitCode")}
    return None


def verify_static_environment(expected_sha256=None):
    if sys.platform != "linux" or os.geteuid() == 0:
        raise ProceduralError("Use Linux e usuário normal; não execute o harness com sudo.")
    if socket.gethostname() != "benchmark-docker":
        raise ProceduralError("Este arquivo só pode executar na VM benchmark-docker.")
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise ProceduralError(f"Python esperado: {EXPECTED_PYTHON}; atual: {sys.version}.")
    source = Path(__file__).resolve()
    if source != EXPECTED_SCRIPT_PATH:
        raise ProceduralError(f"Instale somente este arquivo em {EXPECTED_SCRIPT_PATH}.")
    digest = sha256_file(source)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ProceduralError("O SHA-256 do harness não corresponde ao informado.")
    return digest


def prepare_context(campaign, source_hash):
    account = pwd.getpwuid(os.getuid())
    env = {
        "HOME": account.pw_dir, "USER": account.pw_name, "LOGNAME": account.pw_name,
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
    }
    context = {
        "config": dict(CONFIG), "env": env, "source_hash": source_hash,
        "campaign_id": campaign["campaign_id"], "root": SCRIPT_DIR / "docker-start-reused"
        / campaign["mode"] / campaign["campaign_id"],
        "container_name": CONTAINER_PREFIX + campaign["campaign_id"],
        "container_id": None, "create_attempted": False, "start_attempts": 0,
        "preparation": {}, "provenance": {}, "temporary_dir": None,
    }
    context["docker_base"] = [executable_path("sudo", env), "-n", "--",
                              executable_path("docker", env), "--host", DOCKER_SOCKET]
    return context


def verify_docker(context):
    code, out, err = docker_command(context, ["image", "inspect", DOCKER_IMAGE_ID])
    if code != 0:
        raise ProceduralError(f"Imagem imutável ausente; não será baixada: {err}")
    images = json.loads(out)
    if not isinstance(images, list) or len(images) != 1 or images[0].get("Id") != DOCKER_IMAGE_ID:
        raise ProceduralError("A imagem observada não corresponde ao ID esperado.")
    image = images[0]
    cfg = image.get("Config") or {}
    user = str(cfg.get("User") or "")
    if not user or user.split(":", 1)[0] in {"root", "0"}:
        raise ProceduralError("A imagem deve definir usuário não root.")
    # Não criar volumes anônimos persistentes implicitamente.
    if cfg.get("Volumes"):
        raise ProceduralError("Imagem com volumes declarados fora do protocolo.")
    context["provenance"]["docker_image"] = {
        "id": image["Id"], "os": image.get("Os"), "architecture": image.get("Architecture"),
        "entrypoint": cfg.get("Entrypoint"), "cmd": cfg.get("Cmd"), "user": user,
    }
    context["provenance"]["docker_binary_sha256_reference"] = DOCKER_BINARY_SHA256
    context["provenance"]["docker_binary_hash_verified"] = False
    code, out, err = docker_command(context, ["version", "--format", "{{json .}}"])
    if code != 0:
        raise ProceduralError(f"Não foi possível obter a versão Docker: {err}")
    context["provenance"]["docker_version"] = json.loads(out)
    if os.path.lexists(context["root"]):
        raise ProceduralError("Diretório desta campanha já existe; não será reutilizado.")
    assert_environment_idle(context)


def create_container(context):
    check_stop()
    directory = Path(tempfile.mkdtemp(prefix="tcc-docker-start-"))
    context["temporary_dir"] = directory
    args = [
        "container", "create", "--name", context["container_name"],
        "--label", f"{OWNER_LABEL}={context['campaign_id']}",
        "--label", f"{EXPERIMENT_LABEL}={EXPERIMENT}",
        "--cidfile", str(directory / "container.cid"),
        "--pull=never", "--restart=no", "--read-only", "--cap-drop=ALL",
        "--security-opt", "no-new-privileges:true", "--pids-limit=128",
        "--network", "bridge", "--publish", f"{CONFIG['host']}:{CONFIG['port']}:3000",
        DOCKER_IMAGE_ID,
    ]
    prep = context["preparation"]
    prep.update({"command": context["docker_base"] + args, "completed": False,
                 "wall_started_at": utc_now(), "monotonic_start_ns": time.monotonic_ns()})
    context["create_attempted"] = True
    try:
        code, out, err = docker_command(context, args)
        prep.update({"returncode": code, "stdout": out, "stderr": err})
        if code != 0:
            raise ProceduralError(f"Criação do contêiner falhou: {err}")
        obj = inspect_container(context, context["container_name"])
        if obj is None:
            raise ProceduralError("Docker create retornou sucesso sem contêiner confirmado.")
        cid = assert_owned_container(obj, context)
        # Registra o ID antes de outras verificações: cleanup continua por posse provada.
        context["container_id"] = cid
        recorded = (directory / "container.cid").read_text(encoding="ascii").strip()
        if out != cid or recorded != cid:
            raise ProceduralError("ID retornado, CID file e inspect divergem.")
        prep["container_state"] = assert_environment_idle(context, allow_owned=True)
        prep["completed"] = True
    finally:
        prep["monotonic_end_ns"] = time.monotonic_ns()
        prep["wall_finished_at"] = utc_now()


def cleanup_policy():
    return {
        "version": "conservative-stop-v2",
        "container_stop_grace_seconds": CONTAINER_STOP_GRACE_SECONDS,
        "docker_stop_command_timeout_seconds": DOCKER_STOP_COMMAND_TIMEOUT_SECONDS,
        "aux_process_timeout_seconds": AUX_PROCESS_TIMEOUT_SECONDS,
        "state_confirmation_timeout_seconds_per_stage": CONTAINER_STATE_CONFIRM_TIMEOUT_SECONDS,
        "port_close_timeout_seconds": PORT_CLOSE_TIMEOUT_SECONDS,
        "command_timeout_invalidates_sample": True,
        "required_state_between_attempts": "exited",
        "forced_definition": "docker kill de contingência solicitado explicitamente pelo harness",
    }


def container_state_snapshot(obj):
    state = obj.get("State") or {}
    return {key: state.get(key) for key in
            ("Status", "Running", "Paused", "Restarting", "Pid", "ExitCode",
             "OOMKilled", "Dead", "Error", "StartedAt", "FinishedAt")}


def stopped_state_is_acceptable(state, for_reuse):
    allowed = {"exited"} if for_reuse else {"created", "exited"}
    return (state.get("Status") in allowed and state.get("Running") is False
            and not state.get("Paused") and not state.get("Restarting")
            and not state.get("Dead") and state.get("Pid") == 0)


def confirm_container_stopped(context, cid, cleanup, stage, for_reuse):
    """Confirma por inspect; nunca altera posse nem reclassifica erro anterior."""
    deadline = time.monotonic() + CONTAINER_STATE_CONFIRM_TIMEOUT_SECONDS
    checks = cleanup.setdefault("state_confirmation_checks", [])
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        observed_ns = time.monotonic_ns()
        try:
            obj = inspect_container(context, cid, timeout=min(DOCKER_COMMAND_TIMEOUT_SECONDS, remaining))
            if obj is None:
                raise ProceduralError("Contêiner desapareceu durante a confirmação de parada.")
            assert_owned_container(obj, context)
        except Exception as exc:
            checks.append({"stage": stage, "monotonic_start_ns": observed_ns,
                           "monotonic_end_ns": time.monotonic_ns(), "error": str(exc)})
            raise
        state = container_state_snapshot(obj)
        checks.append({"stage": stage, "monotonic_start_ns": observed_ns,
                       "monotonic_end_ns": time.monotonic_ns(), "state": state})
        cleanup["container_state_after_stop"] = state
        cleanup["container_status_after_stop"] = state["Status"]
        if stopped_state_is_acceptable(state, for_reuse):
            return True
        if state.get("Status") in {"dead", "removing"} or state.get("Dead") or state.get("Paused"):
            raise ProceduralError("Estado final não permite parada/reutilização pelo protocolo.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.100, remaining))


def stop_container(context, cleanup, for_reuse=True):
    cleanup["policy"] = cleanup_policy()
    cleanup["stop_outcome"] = "inconclusive"
    obj = inspect_container(context, context["container_id"])
    if obj is None:
        raise ProceduralError("Contêiner desapareceu antes da parada.")
    cid = assert_owned_container(obj, context)
    state = container_state_snapshot(obj)
    cleanup["container_state_before_cleanup"] = state
    cleanup["container_status_before_cleanup"] = state["Status"]
    cleanup["container_exit_code_before_cleanup"] = state["ExitCode"]
    errors = []
    if state["Running"]:
        operation = {}
        cleanup["docker_stop_command"] = operation
        try:
            code, _, err = docker_command(
                context, ["container", "stop", "--time", str(int(CONTAINER_STOP_GRACE_SECONDS)), cid],
                timeout=DOCKER_STOP_COMMAND_TIMEOUT_SECONDS, interruptible=False, observation=operation,
            )
            if code != 0:
                errors.append(f"Docker stop retornou {code}: {err}")
        except Exception as exc:
            errors.append(f"Docker stop inconclusivo: {exc}")
        confirmed = confirm_container_stopped(context, cid, cleanup, "after_stop", for_reuse)
        if not confirmed:
            # Reconfirma a posse antes de enviar SIGKILL apenas ao contêiner próprio.
            current = inspect_container(context, cid)
            if current is None:
                raise ProceduralError("Contêiner desapareceu antes da contingência.")
            assert_owned_container(current, context)
            current_state = container_state_snapshot(current)
            if stopped_state_is_acceptable(current_state, for_reuse):
                cleanup["container_state_after_stop"] = current_state
                cleanup["container_status_after_stop"] = current_state["Status"]
                confirmed = True
            elif current_state["Running"]:
                cleanup["forced"] = True
                kill_operation = {}
                cleanup["docker_kill_command"] = kill_operation
                code, _, err = docker_command(context, ["container", "kill", cid],
                    interruptible=False, observation=kill_operation)
                if code != 0:
                    raise ProceduralError(f"Falha ao encerrar contêiner próprio: {err}")
                confirmed = confirm_container_stopped(context, cid, cleanup, "after_kill", for_reuse)
    else:
        cleanup["docker_stop_command"] = {"outcome": "not_required"}
        confirmed = confirm_container_stopped(context, cid, cleanup, "already_stopped", for_reuse)
    if not confirmed:
        raise ProceduralError("Parada completa no estado exigido não foi confirmada.")
    state = cleanup["container_state_after_stop"]
    cleanup["container_exit_code_after_stop"] = state["ExitCode"]
    cleanup["container_oom_killed_after_stop"] = state["OOMKilled"]
    cleanup["forced_or_exit_137"] = cleanup["forced"] or state["ExitCode"] == 137
    cleanup["container_stopped"] = True
    cleanup["container_preserved_for_reuse"] = for_reuse
    outcome = cleanup["docker_stop_command"].get("outcome")
    cleanup["stop_outcome"] = (
        "cli_timeout_container_stopped" if outcome == "timeout" else
        "cli_error_container_stopped" if errors else
        "already_stopped" if outcome == "not_required" else "stopped_confirmed"
    )
    if errors:
        raise ProceduralError(" | ".join(errors))

def cleanup_runtime(context, runtime, cleanup):
    errors = []
    if runtime is not None and runtime["process"] is not None:
        process = runtime["process"]
        code = None
        try:
            code = wait_child(process, STARTUP_TIMEOUT_SECONDS, False)
        except Exception as exc:
            errors.append(f"Estado do CLI: {exc}")
        try:
            state = stop_owned_process(process)
            cleanup["runtime_returncode"] = state["returncode"]
            cleanup["cli_forced"] = state["forced"]
        except Exception as exc:
            errors.append(f"Sessão do CLI: {exc}")
        cleanup["docker_cli_exit_before_cleanup"] = code
        if code != 0:
            errors.append(f"Docker start inconclusivo ou falhou: {code}")
        elif read_tail(runtime["stdout_file"]).strip() != context["container_id"]:
            errors.append("ID retornado por docker start diverge do contêiner.")
    try:
        stop_container(context, cleanup)
    except Exception as exc:
        errors.append(f"Parada: {exc}")
    try:
        wait_port_closed(context["config"])
        cleanup["port_closed"] = True
    except Exception as exc:
        errors.append(f"Porta: {exc}")
    if errors:
        raise ProceduralError(" | ".join(errors))
    cleanup["complete"] = True


def validate_result(result):
    validate_timing_result(result)
    if result["success"] and result["failure_reason"] is not None:
        raise ProceduralError("Sucesso não pode conter motivo de falha.")
    if result["sample_valid"]:
        preparation = result["container_preparation"]
        if (not preparation.get("completed") or result["monotonic_start_ns"] is None
                or preparation["monotonic_end_ns"] > result["monotonic_start_ns"]
                or not result["cleanup"]["container_stopped"]
                or not result["cleanup"]["container_preserved_for_reuse"]
                or result["cleanup"].get("container_status_after_stop") != "exited"
                or result["cleanup"]["container_removal_confirmed"]
                or result["runtime_command"][-2:] != ["start", result["container_id"]]):
            raise ProceduralError("Medição não corresponde a start de contêiner preparado.")
        expected = "created" if result["prior_start_attempts"] == 0 else "exited"
        if result["container_state_before_start"]["Status"] != expected:
            raise ProceduralError("Estado inicial de reutilização inconsistente.")
        if result["phase"] != "conditioning" and result["prior_start_attempts"] < 1:
            raise ProceduralError("Medição oficial/piloto sem partida de condicionamento anterior.")


def run_one(context, phase, repetition, campaign, records):
    sample_id = uuid.uuid4().hex
    result = {
        "schema_version": SCHEMA_VERSION, "experiment": EXPERIMENT,
        "campaign_id": campaign["campaign_id"], "sample_id": sample_id,
        "environment": "docker", "hostname": socket.gethostname(),
        "phase": phase, "mode": campaign["mode"], "repetition": repetition,
        "url": context["config"]["url"], "measurement_definition": MEASUREMENT_DEFINITION,
        "container_policy": "single_container_reused_after_conditioning",
        "container_id": context["container_id"], "container_name": context["container_name"],
        "container_preparation": dict(context["preparation"]),
        "prior_start_attempts": context["start_attempts"], "container_state_before_start": None,
        "wall_timestamp_start": None, "wall_timestamp_ready": None, "wall_timestamp_end": None,
        "monotonic_start_ns": None, "monotonic_ready_ns": None,
        "monotonic_measurement_end_ns": None, "cold_start_ns": None, "cold_start_ms": None,
        "elapsed_ms": None, "probe_count": 0, "probe_attempts": [],
        "max_probe_start_gap_ms": None, "max_probe_duration_ms": None,
        "http_status": None, "response_body": None, "content_type": None,
        "success": False, "sample_valid": False, "failure_reason": None,
        "procedural_error": None, "runtime_command": None, "runtime_identifier": context["container_id"],
        "runtime_returncode_before_cleanup": None, "harness_sha256": context["source_hash"],
        "baseline_harness_sha256": BASELINE_HARNESS_SHA256,
        "harness_version": HARNESS_VERSION, "parent_harness_sha256": PARENT_HARNESS_SHA256,
        "cleanup_policy": cleanup_policy(),
        "docker_image_id": DOCKER_IMAGE_ID, "artifact_sha256_expected": DOCKER_BINARY_SHA256,
        "artifact_sha256_observed": None, "docker_binary_hash_verified": False,
        "python_version": sys.version.split()[0], "startup_timeout_ms": STARTUP_TIMEOUT_SECONDS * 1000,
        "probe_timeout_ms": PROBE_TIMEOUT_SECONDS * 1000, "probe_interval_ms": PROBE_INTERVAL_SECONDS * 1000,
        "probe_interval_definition": "sono após probe inválido; não é frequência fixa",
        "cooldown_seconds": COOLDOWN_SECONDS, "stabilization_seconds": STABILIZATION_SECONDS,
        "cleanup": {"complete": False, "forced": False, "runtime_returncode": None,
                    "container_stopped": False, "container_preserved_for_reuse": False,
                    "container_removal_confirmed": False, "port_closed": False},
    }
    runtime = {"process": None, "stdout_file": None, "stderr_file": None}
    errors = []
    try:
        check_stop()
        if sha256_file(EXPECTED_SCRIPT_PATH) != context["source_hash"]:
            raise ProceduralError("Harness alterado durante a campanha.")
        if phase != "conditioning" and context["start_attempts"] == 0:
            raise ProceduralError("Condicionamento deve preceder as medições.")
        if phase == "conditioning" and context["start_attempts"] != 0:
            raise ProceduralError("O condicionamento deve ser a primeira partida.")
        result["container_state_before_start"] = assert_environment_idle(context, allow_owned=True)
        runtime["stdout_file"] = tempfile.TemporaryFile()
        runtime["stderr_file"] = tempfile.TemporaryFile()
        command = context["docker_base"] + ["start", context["container_id"]]
        result["runtime_command"] = command
        check_stop()
        context["start_attempts"] += 1
        result["wall_timestamp_start"] = utc_now()
        start_ns = time.monotonic_ns()
        result["monotonic_start_ns"] = start_ns
        runtime["process"] = subprocess.Popen(
            command, cwd=PROJECT_DIR, env=context["env"], stdin=subprocess.DEVNULL,
            stdout=runtime["stdout_file"], stderr=runtime["stderr_file"], start_new_session=True,
        )
        deadline_ns = start_ns + int(STARTUP_TIMEOUT_SECONDS * 1_000_000_000)
        previous_start_ns = None
        while True:
            check_stop()
            if time.monotonic_ns() >= deadline_ns:
                result["failure_reason"] = "startup_timeout"
                break
            code = child_exit_code(runtime["process"])
            if code is not None:
                result["runtime_returncode_before_cleanup"] = code
                if code != 0:
                    raise ProceduralError(f"Docker start falhou (código {code}).")
            probe = probe_once(context["config"]["url"], min(deadline_ns,
                time.monotonic_ns() + int(PROBE_TIMEOUT_SECONDS * 1_000_000_000)))
            probe["index"] = len(result["probe_attempts"]) + 1
            probe["start_offset_ns"] = probe["start_ns"] - start_ns
            probe["start_gap_ns"] = None if previous_start_ns is None else probe["start_ns"] - previous_start_ns
            previous_start_ns = probe["start_ns"]
            result["probe_attempts"].append(probe)
            if probe["valid"] and probe["end_ns"] < deadline_ns:
                result["success"] = True
                result["monotonic_ready_ns"] = probe["end_ns"]
                result["cold_start_ns"] = probe["end_ns"] - start_ns
                result["cold_start_ms"] = result["cold_start_ns"] / 1_000_000
                result["wall_timestamp_ready"] = utc_now()
                break
            remaining = deadline_ns - time.monotonic_ns()
            if remaining > 0:
                time.sleep(min(PROBE_INTERVAL_SECONDS, remaining / 1_000_000_000))
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        if result["monotonic_start_ns"] is not None:
            end_ns = time.monotonic_ns()
            result["monotonic_measurement_end_ns"] = end_ns
            result["elapsed_ms"] = (end_ns - result["monotonic_start_ns"]) / 1_000_000
        result["wall_timestamp_end"] = utc_now()
        probes = result["probe_attempts"]
        result["probe_count"] = len(probes)
        if probes:
            result["max_probe_duration_ms"] = max(p["duration_ns"] for p in probes) / 1_000_000
            result["http_status"] = probes[-1]["status"]
            result["response_body"] = probes[-1]["body"]
            result["content_type"] = probes[-1]["content_type"]
        if len(probes) > 1:
            result["max_probe_start_gap_ms"] = max(p["start_gap_ns"] for p in probes[1:]) / 1_000_000
        try:
            cleanup_runtime(context, runtime, result["cleanup"])
        except Exception as exc:
            errors.append(f"Cleanup: {exc}")
        result["runtime_logs"] = {}
        for name in ("stdout_file", "stderr_file"):
            file = runtime[name]
            if file is not None:
                try:
                    result["runtime_logs"][name.replace("_file", "_tail")] = read_tail(file)
                except Exception as exc:
                    errors.append(f"Logs: {exc}")
                finally:
                    try:
                        file.close()
                    except OSError as exc:
                        errors.append(f"Fechamento dos logs: {exc}")
        if STOP_SIGNAL is not None:
            errors.append(f"Interrupção solicitada pelo sinal {STOP_SIGNAL}.")
    if not result["success"] and result["failure_reason"] is None:
        result["failure_reason"] = "procedural_error"
    result["sample_valid"] = not errors
    result["procedural_error"] = " | ".join(errors) if errors else None
    try:
        validate_result(result)
    except Exception as exc:
        errors.append(f"Validação do JSON: {exc}")
        result["sample_valid"] = False
        result["procedural_error"] = " | ".join(errors)
    if errors:
        target = context["root"] / "discarded" / f"{phase}-{sample_id}.json"
    elif phase == "conditioning":
        target = context["root"] / "conditioning.json"
    else:
        target = context["root"] / "samples" / f"r{repetition:02d}.json"
    try:
        record = write_json_exclusive(target, result)
    except PublicationError as exc:
        records.append({"path": str(exc.path), "sha256": exc.digest,
                        "publication_confirmed": False, "destination_published": exc.published,
                        "sample_valid": False, "phase": phase, "repetition": repetition,
                        "publication_error": str(exc), "result_snapshot": result})
        raise
    record.update({"sample_id": sample_id, "phase": phase, "repetition": repetition,
                   "sample_valid": result["sample_valid"], "success": result["success"],
                   "failure_reason": result["failure_reason"]})
    records.append(record)
    if errors:
        raise ProceduralError(result["procedural_error"])
    return result


def cleanup_campaign(context):
    cleanup = {"complete": False, "forced": False, "container_removal_confirmed": False,
               "port_closed": False}
    if not context["create_attempted"]:
        cleanup.update({"complete": True, "not_required": True})
        return cleanup
    try:
        obj = inspect_container(context, context["container_name"])
        if obj is None:
            deadline = time.monotonic() + DOCKER_RECONCILE_SECONDS
            while obj is None and time.monotonic() < deadline:
                time.sleep(0.100)
                obj = inspect_container(context, context["container_name"])
        if obj is None:
            raise ProceduralError("Criação/remoção não reconciliada; verificar o nome registrado antes de nova coleta.")
        cid = assert_owned_container(obj, context)
        context["container_id"] = cid
        stop_container(context, cleanup, for_reuse=False)
        # Nunca usa rm -f/prune, nem remove por filtro; somente o ID próprio confirmado.
        code, _, err = docker_command(context, ["container", "rm", cid], interruptible=False)
        if code != 0:
            raise ProceduralError(f"Remoção do contêiner falhou: {err}")
        if (inspect_container(context, cid) is not None
                or inspect_container(context, context["container_name"]) is not None):
            raise ProceduralError("Remoção do contêiner não confirmada.")
        cleanup["container_removal_confirmed"] = True
        cleanup["container_preserved_for_reuse"] = False
        wait_port_closed(context["config"])
        cleanup["port_closed"] = True
        directory = context["temporary_dir"]
        if directory is not None:
            (directory / "container.cid").unlink(missing_ok=True)
            directory.rmdir()
        cleanup["complete"] = True
    except Exception as exc:
        cleanup["error"] = f"{type(exc).__name__}: {exc}"
        cleanup["preserved_cid_directory"] = str(context["temporary_dir"])
    return cleanup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["pilot", "official"], help="2 ou 30 tentativas, além do condicionamento")
    parser.add_argument("--expected-sha256", help="opcional: hash deste arquivo conferido antes da transferência")
    args = parser.parse_args()
    if args.expected_sha256 is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", args.expected_sha256):
        parser.error("--expected-sha256 deve conter 64 dígitos hexadecimais")
    # As proteções de instalação/host são verificadas antes de criar o lock ou arquivos.
    try:
        source_hash = verify_static_environment(
            args.expected_sha256.lower() if args.expected_sha256 else None)
    except Exception as exc:
        print(f"ABORTADO antes da preparação: {exc}", file=sys.stderr)
        return 1
    repetitions = PILOT_REPETITIONS if args.mode == "pilot" else OFFICIAL_REPETITIONS
    records = []
    campaign = {
        "schema_version": SCHEMA_VERSION, "experiment": EXPERIMENT,
        "campaign_id": uuid.uuid4().hex, "environment": "docker", "mode": args.mode,
        "expected_repetitions": repetitions, "completed_repetitions": 0, "real_failures": 0,
        "started_at": utc_now(), "finished_at": None, "complete": False,
        "error": None, "stop_signal": None, "files": records,
        "measurement_definition": MEASUREMENT_DEFINITION,
        "container_policy": "single_container_reused_after_conditioning",
        "harness_sha256": source_hash, "baseline_harness_sha256": BASELINE_HARNESS_SHA256,
        "harness_version": HARNESS_VERSION, "parent_harness_sha256": PARENT_HARNESS_SHA256,
        "cleanup_policy": cleanup_policy(),
    }
    context = None
    lock_fd = None
    previous_handlers = {}
    exit_code = 1
    try:
        if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
            raise ProceduralError("Já existe um temporizador SIGALRM ativo.")
        for signum, handler in ((signal.SIGINT, request_stop), (signal.SIGTERM, request_stop),
                                (signal.SIGALRM, alarm_expired)):
            previous_handlers[signum] = signal.signal(signum, handler)
        lock_fd = acquire_lock()  # Mesmo lock do harness histórico.
        context = prepare_context(campaign, source_hash)
        verify_docker(context)
        campaign["host"] = host_metadata()
        campaign["runtime_provenance"] = context["provenance"]
        print(f"Preflight concluído. SHA-256: {source_hash}", flush=True)
        print("Estabilização: 60 s. Criação fora da janela; um contêiner reutilizado.", flush=True)
        sleep_interruptible(STABILIZATION_SECONDS)
        create_container(context)
        conditioning = run_one(context, "conditioning", None, campaign, records)
        if not conditioning["success"]:
            raise ProceduralError("Condicionamento sem prontidão; campanha interrompida.")
        sleep_interruptible(COOLDOWN_SECONDS)
        for repetition in range(1, repetitions + 1):
            result = run_one(context, args.mode, repetition, campaign, records)
            print(f"r{repetition:02d}: " + (f"{result['cold_start_ms']:.3f} ms" if result["success"]
                  else f"falha real ({result['failure_reason']})"), flush=True)
            if repetition < repetitions:
                sleep_interruptible(COOLDOWN_SECONDS)
        check_stop()
        if sha256_file(EXPECTED_SCRIPT_PATH) != source_hash:
            raise ProceduralError("Harness alterado antes do fechamento da campanha.")
        campaign["complete"] = True
    except Exception as exc:
        campaign["error"] = f"{type(exc).__name__}: {exc}"
        print(f"ABORTADO: {campaign['error']}", file=sys.stderr, flush=True)
    finally:
        campaign["completed_repetitions"] = sum(
            r.get("phase") == args.mode and r.get("sample_valid") is True
            and r.get("publication_confirmed") is True for r in records)
        campaign["real_failures"] = sum(
            r.get("phase") == args.mode and r.get("sample_valid") is True
            and r.get("success") is False and r.get("publication_confirmed") is True for r in records)
        if context is not None:
            campaign["container_preparation"] = context["preparation"]
            campaign["container_id"] = context["container_id"]
            campaign["container_name"] = context["container_name"]
            campaign["start_attempts"] = context["start_attempts"]
            campaign["cleanup"] = cleanup_campaign(context)
            if not campaign["cleanup"]["complete"]:
                campaign["complete"] = False
                detail = "Cleanup final: " + campaign["cleanup"].get("error", "inconclusivo")
                campaign["error"] = (campaign["error"] + " | " if campaign["error"] else "") + detail
        if campaign["completed_repetitions"] != repetitions or STOP_SIGNAL is not None:
            campaign["complete"] = False
        campaign["stop_signal"] = STOP_SIGNAL
        campaign["finished_at"] = utc_now()
        if campaign["complete"] and campaign["error"] is None:
            exit_code = 2 if campaign["real_failures"] else 0
        if context is not None:
            try:
                manifest = context["root"] / "manifest.json"
                write_json_exclusive(manifest, campaign)
                print(f"Manifesto: {manifest}", flush=True)
            except Exception as exc:
                exit_code = 1
                print(f"Falha ao publicar manifesto: {exc}", file=sys.stderr, flush=True)
        if lock_fd is not None:
            os.close(lock_fd)
        # Não cancela temporizador alheio quando a instalação dos handlers falha.
        if signal.SIGALRM in previous_handlers:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
