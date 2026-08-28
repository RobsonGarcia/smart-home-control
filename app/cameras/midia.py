"""
ffmpeg: transformar RTSP no que o navegador toca.

Nenhum navegador toca RTSP. Duas conversões acontecem aqui:

- **Foto**: um quadro do fluxo virando JPEG. Só é usada quando a câmera não
  tem snapshot ONVIF próprio (que é HTTP puro e não custa processo nenhum).
- **Ao vivo**: HLS, um processo por câmera, começando quando alguém abre a
  página e **morrendo sozinho** quando ninguém mais pede a playlist. Sem esse
  encerramento por inatividade, sete câmeras abertas uma vez viram sete
  ffmpegs eternos.

Primeiro tentamos `-c:v copy`: sem transcodificar, o custo de CPU é
praticamente zero — a câmera já entrega H.264 e nós só reembalamos. Se a
playlist não aparecer (fluxo em H.265, que o HLS do navegador não toca), a
sessão renasce transcodificando, e a câmera fica marcada para já nascer assim
na próxima vez.
"""

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from app.config import (
    HLS_DIR,
    HLS_INATIVIDADE_SEGUNDOS,
    HLS_SEGMENTOS_NA_LISTA,
    HLS_SEGMENTO_SEGUNDOS,
)
from app.errors import ValidationError

logger = logging.getLogger(__name__)

SEGUNDOS_ATE_A_PLAYLIST = 12      # paciência antes de considerar que não vai
_ffmpeg_cache: list = []


def caminho_ffmpeg() -> Optional[str]:
    """
    O ffmpeg disponível: o do PATH, senão o que vem com `imageio-ffmpeg`.

    O pacote traz um binário estático próprio — é por isso que o painel não
    exige instalar ffmpeg no sistema.
    """
    if _ffmpeg_cache:
        return _ffmpeg_cache[0]

    caminho = shutil.which("ffmpeg")
    if not caminho:
        try:
            import imageio_ffmpeg
            caminho = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            caminho = None
    _ffmpeg_cache.append(caminho)
    return caminho


def ffmpeg_disponivel() -> bool:
    return caminho_ffmpeg() is not None


def _entrada(url: str) -> list:
    """
    Opções que dependem do protocolo da entrada.

    `-rtsp_transport tcp` força o vídeo por TCP: em UDP, câmera com Wi-Fi
    fraco entrega quadros picotados. Só se aplica a RTSP — e é o que permite
    apontar este mesmo caminho para um arquivo local, que é como o pipeline
    é testado sem câmera nenhuma.
    """
    return ["-rtsp_transport", "tcp"] if url.lower().startswith("rtsp") else []


def _exigir_ffmpeg() -> str:
    caminho = caminho_ffmpeg()
    if not caminho:
        raise ValidationError(
            "Vídeo precisa do ffmpeg. Rode: pip install imageio-ffmpeg "
            "(ou instale o ffmpeg no sistema) e reinicie o painel.")
    return caminho


def snapshot_de_rtsp(url: str, timeout: int = 15) -> bytes:
    """Um quadro do fluxo, em JPEG. Para câmera sem snapshot ONVIF."""
    comando = [
        _exigir_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
        *_entrada(url), "-i", url,
        "-frames:v", "1", "-q:v", "3", "-f", "image2", "-vcodec", "mjpeg",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(comando, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ValidationError("A câmera não entregou imagem a tempo.")

    if not proc.stdout:
        # A URL tem senha embutida: só a última linha do erro do ffmpeg sai
        # daqui, nunca a linha de comando.
        erro = (proc.stderr or b"").decode("utf-8", "replace").strip()
        detalhe = erro.splitlines()[-1] if erro else "sem detalhe do ffmpeg"
        raise ValidationError("Não consegui extrair um quadro do vídeo: %s"
                              % detalhe)
    return proc.stdout


# ---------------------------------------------------------------- sessões

class _Sessao:
    def __init__(self, device_id: str, url: str, transcodificar: bool):
        self.device_id = device_id
        self.url = url
        self.transcodificar = transcodificar
        self.pasta = Path(HLS_DIR) / device_id
        self.processo: Optional[subprocess.Popen] = None
        self.ultimo_acesso = time.monotonic()
        self.iniciada_em = time.monotonic()

    @property
    def playlist(self) -> Path:
        return self.pasta / "index.m3u8"

    def iniciar(self):
        if self.pasta.exists():
            shutil.rmtree(self.pasta, ignore_errors=True)
        self.pasta.mkdir(parents=True, exist_ok=True)

        video = (["-c:v", "libx264", "-preset", "veryfast", "-tune",
                  "zerolatency", "-g", "25", "-pix_fmt", "yuv420p"]
                 if self.transcodificar else ["-c:v", "copy"])
        comando = [
            _exigir_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error",
            "-fflags", "nobuffer", *_entrada(self.url), "-i", self.url,
            "-an", *video, "-f", "hls",
            "-hls_time", str(HLS_SEGMENTO_SEGUNDOS),
            "-hls_list_size", str(HLS_SEGMENTOS_NA_LISTA),
            "-hls_flags", "delete_segments+omit_endlist+independent_segments",
            "-hls_segment_filename", str(self.pasta / "seg%d.ts"),
            str(self.playlist),
        ]
        # stderr vai para arquivo, não para PIPE: um PIPE que ninguém lê
        # enche e trava o ffmpeg — e o log some junto com o processo.
        self._log = open(self.pasta / "ffmpeg.log", "wb")
        self.processo = subprocess.Popen(comando, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL,
                                         stderr=self._log)
        logger.info("hls: sessão iniciada para %s (%s)", self.device_id,
                    "transcodificando" if self.transcodificar else "cópia")

    def viva(self) -> bool:
        return self.processo is not None and self.processo.poll() is None

    def ultimo_erro(self) -> str:
        """A última linha do log do ffmpeg — o diagnóstico de 'não abriu'."""
        try:
            linhas = (self.pasta / "ffmpeg.log").read_text(
                encoding="utf-8", errors="replace").strip().splitlines()
            return linhas[-1] if linhas else ""
        except OSError:
            return ""

    def encerrar(self):
        if self.processo and self.processo.poll() is None:
            self.processo.terminate()
            try:
                self.processo.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.processo.kill()
        log = getattr(self, "_log", None)
        if log and not log.closed:
            log.close()
        shutil.rmtree(self.pasta, ignore_errors=True)
        logger.info("hls: sessão de %s encerrada", self.device_id)


_sessoes: Dict[str, _Sessao] = {}
_trava = threading.Lock()
_faxineira: Optional[threading.Thread] = None
# Câmeras cujo fluxo não deu para copiar: já nascem transcodificando.
_precisa_transcodificar = set()


def _faxina():
    """Encerra o que ninguém está assistindo. Um processo por câmera, no máximo."""
    while True:
        time.sleep(5)
        agora = time.monotonic()
        with _trava:
            for device_id, sessao in list(_sessoes.items()):
                inativa = agora - sessao.ultimo_acesso > HLS_INATIVIDADE_SEGUNDOS
                if inativa or not sessao.viva():
                    sessao.encerrar()
                    del _sessoes[device_id]
            if not _sessoes:
                return  # ninguém assistindo: a thread some até a próxima sessão


def _garantir_faxineira():
    global _faxineira
    if _faxineira is None or not _faxineira.is_alive():
        _faxineira = threading.Thread(target=_faxina, daemon=True,
                                      name="hls-faxina")
        _faxineira.start()


def playlist_de(device_id: str, url: str) -> Path:
    """
    A playlist HLS desta câmera, iniciando a sessão se preciso.

    Bloqueia até o primeiro segmento existir (ou desistir): devolver um m3u8
    que ainda não foi escrito faz o player errar e desistir sozinho.
    """
    _exigir_ffmpeg()
    with _trava:
        sessao = _sessoes.get(device_id)
        if sessao and sessao.viva():
            sessao.ultimo_acesso = time.monotonic()
            if sessao.playlist.exists():
                return sessao.playlist
        else:
            if sessao:
                sessao.encerrar()
            sessao = _Sessao(device_id, url,
                             device_id in _precisa_transcodificar)
            sessao.iniciar()
            _sessoes[device_id] = sessao
        _garantir_faxineira()

    if _esperar_playlist(sessao):
        return sessao.playlist

    # Não saiu nada com cópia: quase sempre é fluxo que o HLS não aceita
    # reembalado. Uma segunda tentativa, transcodificando.
    if not sessao.transcodificar:
        logger.info("hls: %s não saiu em cópia, transcodificando", device_id)
        _precisa_transcodificar.add(device_id)
        with _trava:
            sessao.encerrar()
            sessao = _Sessao(device_id, url, True)
            sessao.iniciar()
            _sessoes[device_id] = sessao
        if _esperar_playlist(sessao):
            return sessao.playlist

    detalhe = sessao.ultimo_erro()
    with _trava:
        sessao.encerrar()
        _sessoes.pop(device_id, None)
    raise ValidationError(
        "Não consegui abrir o vídeo desta câmera. Confira usuário, senha e a "
        "URL RTSP — e se a câmera aceita mais um espectador simultâneo.%s"
        % (" Erro do ffmpeg: %s" % detalhe if detalhe else ""))


def _esperar_playlist(sessao: _Sessao) -> bool:
    limite = time.monotonic() + SEGUNDOS_ATE_A_PLAYLIST
    while time.monotonic() < limite:
        if sessao.playlist.exists() and any(sessao.pasta.glob("seg*.ts")):
            return True
        if not sessao.viva():
            return False
        time.sleep(0.25)
    return False


def marcar_acesso(device_id: str):
    """Alguém ainda está assistindo — adia o encerramento por inatividade."""
    with _trava:
        sessao = _sessoes.get(device_id)
        if sessao:
            sessao.ultimo_acesso = time.monotonic()


def arquivo_da_sessao(device_id: str, nome: str) -> Optional[Path]:
    """Um segmento da sessão, se ela existir. `nome` já vem saneado na rota."""
    with _trava:
        sessao = _sessoes.get(device_id)
        if not sessao:
            return None
        sessao.ultimo_acesso = time.monotonic()
        caminho = sessao.pasta / nome
    return caminho if caminho.exists() else None


def encerrar(device_id: str):
    with _trava:
        sessao = _sessoes.pop(device_id, None)
    if sessao:
        sessao.encerrar()


def sessoes_ativas() -> list:
    with _trava:
        return [{"device_id": s.device_id,
                 "transcodificando": s.transcodificar,
                 "ha_segundos": round(time.monotonic() - s.iniciada_em, 1)}
                for s in _sessoes.values()]
