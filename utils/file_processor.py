import io
import base64
import logging
import aiohttp

logger = logging.getLogger("cyan.file_processor")

SUPPORTED_IMAGES  = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_DOCS    = {".pdf"}
SUPPORTED_TEXT    = {".txt", ".csv", ".md", ".rtf"}
SUPPORTED_AUDIO   = {".mp3", ".mp4", ".wav", ".ogg", ".m4a", ".webm"}
SUPPORTED_VECTOR  = {".ps", ".eps", ".ai"}   # PostScript / Encapsulated PS / Illustrator
SUPPORTED_EDITABLE = {".psd", ".psb"}         # Photoshop — renderizado via psd-tools


class FileProcessor:
    def __init__(
        self,
        max_size_mb: int = 10,
        pdf_max_pages: int = 3,
        pdf_dpi: int = 150,
        openai_api_key: str | None = None,
    ):
        self.max_size_mb = max_size_mb
        self.pdf_max_pages = pdf_max_pages
        self.pdf_dpi = pdf_dpi
        self._openai_api_key = openai_api_key
        self._openai: object | None = None  # AsyncOpenAI, carregado sob demanda

    def _get_openai(self):
        """Retorna (ou cria) o cliente AsyncOpenAI para transcrições de áudio."""
        if self._openai is None:
            from openai import AsyncOpenAI
            self._openai = AsyncOpenAI(api_key=self._openai_api_key)
        return self._openai

    async def process_attachment(self, attachment) -> list[dict]:
        """
        Processa um anexo do Discord e retorna uma lista de partes de conteúdo
        prontas para enviar ao GPT-4o (text / image_url).
        Sempre retorna uma lista — pode ter 1 ou mais itens.
        """
        size_mb = attachment.size / (1024 * 1024)

        if size_mb > self.max_size_mb:
            return [
                {
                    "type": "text",
                    "text": (
                        f"⚠️ Arquivo **{attachment.filename}** ({size_mb:.1f} MB) excede "
                        f"{self.max_size_mb} MB — deve ser enviado via Google Drive."
                    ),
                }
            ]

        ext = self._extension(attachment.filename)

        if ext in SUPPORTED_IMAGES:
            return await self._process_image(attachment)

        if ext in SUPPORTED_DOCS:
            return await self._process_pdf(attachment)

        if ext in SUPPORTED_TEXT:
            return await self._process_text(attachment)

        if ext in SUPPORTED_AUDIO:
            return await self._process_audio(attachment)

        if ext in SUPPORTED_VECTOR:
            return await self._process_vector(attachment)

        if ext in SUPPORTED_EDITABLE:
            return await self._process_psd(attachment.url, attachment.filename)

        # Formato não reconhecido — registra apenas o nome
        return [
            {
                "type": "text",
                "text": (
                    f"📎 Arquivo recebido: **{attachment.filename}** "
                    f"(formato {ext or 'desconhecido'} — não processável visualmente)"
                ),
            }
        ]

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _extension(filename: str) -> str:
        """Retorna a extensão em minúsculas com ponto (ex: '.png')."""
        if "." in filename:
            return "." + filename.rsplit(".", 1)[-1].lower()
        return ""

    async def _process_image(self, attachment) -> list[dict]:
        """
        Baixa a imagem, valida com Pillow, redimensiona se necessário e converte para base64.
        User-Agent é necessário para que alguns CDNs (incluindo Discord) entreguem o conteúdo.
        """
        try:
            from PIL import Image as PILImage

            data = await self._download(attachment.url)
            size_kb = len(data) / 1024

            # Valida e re-encoda com Pillow
            img = PILImage.open(io.BytesIO(data))

            # Capturar metadados ANTES de qualquer conversão (CY7.1)
            formato_real = img.format or "DESCONHECIDO"
            orig_w, orig_h = img.size

            # Mantém transparência como PNG, converte o resto para JPEG
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                fmt, mime = "PNG", "png"
                # Transparência real: canal alpha com ao menos um pixel < 255
                tem_alpha = img.getextrema()[3][0] < 255
            else:
                img = img.convert("RGB")
                fmt, mime = "JPEG", "jpeg"
                tem_alpha = False

            # Redimensiona se maior que 1568px (evita payload enorme)
            MAX_PX = 1568
            if max(img.size) > MAX_PX:
                img.thumbnail((MAX_PX, MAX_PX), PILImage.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format=fmt, quality=90)

            # PNG com transparência pode ficar muito grande — se > 1.5 MB base64,
            # reduz para 1024px e tenta de novo; se ainda grande, converte para JPEG
            # com fundo branco (preserva leitura visual pelo GPT-4o)
            MAX_B64_BYTES = 1_500_000
            if mime == "png" and len(buf.getvalue()) > MAX_B64_BYTES:
                img.thumbnail((1024, 1024), PILImage.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                if len(buf.getvalue()) > MAX_B64_BYTES:
                    # Fundo branco para converter RGBA → JPEG sem artefatos
                    bg = PILImage.new("RGB", img.size, (255, 255, 255))
                    bg.paste(img, mask=img.split()[3])  # usa canal alpha como máscara
                    buf = io.BytesIO()
                    bg.save(buf, format="JPEG", quality=90)
                    img, fmt, mime = bg, "JPEG", "jpeg"
                    logger.info(f"PNG RGBA grande demais → convertido para JPEG com fundo branco: {attachment.filename}")

            b64 = base64.b64encode(buf.getvalue()).decode()

            logger.info(
                f"Imagem OK: {attachment.filename} → {img.size} {fmt} ({len(b64)//1024}KB b64)"
            )

            meta_text = (
                f"[DADOS TÉCNICOS {attachment.filename}: formato {formato_real}, "
                f"{orig_w}×{orig_h} px (original), "
                f"fundo transparente: {'sim' if tem_alpha else 'não'}, "
                f"{size_kb:.0f} KB]"
            )
            return [
                {"type": "text", "text": meta_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/{mime};base64,{b64}",
                        "detail": "high",
                    },
                },
            ]
        except Exception as exc:
            logger.error(f"Erro ao processar imagem {attachment.filename}: {exc}", exc_info=True)
            return [
                {
                    "type": "text",
                    "text": f"⚠️ Não foi possível processar a imagem **{attachment.filename}**: {exc}",
                }
            ]

    async def _process_pdf(self, attachment) -> list[dict]:
        """Converte as primeiras páginas do PDF em imagens via pdf2image."""
        try:
            import shutil
            from pdf2image import convert_from_bytes  # type: ignore

            # Detecta caminho do poppler automaticamente
            pdftoppm = shutil.which("pdftoppm")
            poppler_path = str(__import__("pathlib").Path(pdftoppm).parent) if pdftoppm else "/usr/bin"

            pdf_bytes = await self._download(attachment.url)
            images = convert_from_bytes(
                pdf_bytes,
                dpi=self.pdf_dpi,
                first_page=1,
                last_page=self.pdf_max_pages,
                poppler_path=poppler_path,
            )

            parts: list[dict] = [
                {
                    "type": "text",
                    "text": (
                        f"📄 PDF **{attachment.filename}** "
                        f"— primeiras {len(images)} página(s):"
                    ),
                }
            ]

            for img in images:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    }
                )

            return parts

        except ImportError:
            logger.warning("pdf2image não instalado — usando extração de texto como fallback")
            return await self._pdf_text_fallback(attachment)

        except Exception as exc:
            logger.error(f"Erro ao converter PDF {attachment.filename}: {exc}", exc_info=True)
            return [
                {
                    "type": "text",
                    "text": f"⚠️ Não foi possível processar o PDF **{attachment.filename}**: {exc}",
                }
            ]

    async def _pdf_text_fallback(self, attachment) -> list[dict]:
        """Extrai texto do PDF usando pypdf quando pdf2image não está disponível."""
        try:
            from pypdf import PdfReader  # type: ignore

            pdf_bytes = await self._download(attachment.url)
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages[: self.pdf_max_pages]
            )
            return [
                {
                    "type": "text",
                    "text": (
                        f"📄 PDF **{attachment.filename}** (texto extraído):\n"
                        + text[:3000]
                    ),
                }
            ]

        except Exception as exc:
            logger.error(f"Erro ao extrair texto do PDF {attachment.filename}: {exc}")
            return [
                {
                    "type": "text",
                    "text": (
                        f"⚠️ PDF **{attachment.filename}** recebido mas não processável: {exc}"
                    ),
                }
            ]

    async def _process_text(self, attachment) -> list[dict]:
        """Lê o conteúdo de arquivos de texto (.txt, .csv, etc.)."""
        try:
            raw = await self._download(attachment.url)
            text = raw.decode("utf-8", errors="replace")
            return [
                {
                    "type": "text",
                    "text": f"📄 Arquivo de texto **{attachment.filename}**:\n{text[:4000]}",
                }
            ]
        except Exception as exc:
            logger.error(f"Erro ao ler texto {attachment.filename}: {exc}")
            return [
                {
                    "type": "text",
                    "text": f"⚠️ Não foi possível ler **{attachment.filename}**: {exc}",
                }
            ]

    async def process_from_drive(self, drive_url: str) -> tuple[list[dict], str]:
        """
        Baixa um arquivo do Google Drive, detecta nome/extensão pelo header e processa.
        Retorna (parts, filename).
        """
        import re
        file_id = None
        for pattern in [
            r'drive\.google\.com/open\?id=([a-zA-Z0-9_-]+)',
            r'drive\.google\.com/file/d/([a-zA-Z0-9_-]+)',
            r'drive\.google\.com/uc\?.*id=([a-zA-Z0-9_-]+)',
            r'id=([a-zA-Z0-9_-]+)',
        ]:
            m = re.search(pattern, drive_url)
            if m:
                file_id = m.group(1)
                break

        if not file_id:
            return [{"type": "text", "text": f"⚠️ Não foi possível extrair o ID do link do Drive: {drive_url}"}], "drive_link"

        download_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CyanBot/1.0)"}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url, headers=headers, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    data = await resp.read()

                    # Tenta extrair nome do arquivo do header Content-Disposition
                    disposition = resp.headers.get("Content-Disposition", "")
                    filename = "arquivo_drive"
                    m = re.search(r'filename[^;=\n]*=[\s"\']*([^;\n"\']+)', disposition)
                    if m:
                        filename = m.group(1).strip()
                    else:
                        # Tenta inferir extensão pelo Content-Type
                        ct = resp.headers.get("Content-Type", "")
                        ext_map = {
                            "image/jpeg": ".jpg", "image/png": ".png",
                            "image/webp": ".webp", "application/pdf": ".pdf",
                            "image/gif": ".gif", "application/zip": ".zip",
                        }
                        for mime, ext in ext_map.items():
                            if mime in ct:
                                filename = f"arquivo_drive{ext}"
                                break

        except Exception as exc:
            logger.error(f"Erro ao baixar do Drive {file_id}: {exc}")
            return [{"type": "text", "text": f"⚠️ Não foi possível baixar o arquivo do Google Drive: {exc}"}], "drive_erro"

        logger.info(f"Drive: baixado {filename} ({len(data)//1024}KB) — id={file_id}")
        parts = await self.process_from_url(download_url, filename)
        # process_from_url vai baixar de novo — passa os bytes direto pelo nome detectado
        ext = self._extension(filename)
        if ext in SUPPORTED_IMAGES:
            parts = await self._process_image_bytes(data, filename)
        elif ext in SUPPORTED_DOCS:
            parts = await self._process_pdf_bytes(data, filename)
        elif ext in SUPPORTED_EDITABLE:
            parts = await self._process_psd_bytes(data, filename)
        else:
            parts = [{"type": "text", "text": f"📎 Arquivo do Drive: **{filename}** (formato {ext or 'desconhecido'})"}]

        return parts, filename

    async def process_from_url(self, url: str, filename: str) -> list[dict]:
        """
        Processa um arquivo a partir de uma URL direta (sem objeto attachment).
        Detecta o tipo pelo nome do arquivo e delega ao handler correto.
        """
        ext = self._extension(filename)

        try:
            data = await self._download(url)
        except Exception as exc:
            logger.error(f"Erro ao baixar {filename}: {exc}")
            return [{"type": "text", "text": f"⚠️ Não foi possível baixar **{filename}**: {exc}"}]

        if ext in SUPPORTED_IMAGES:
            return await self._process_image_bytes(data, filename)

        if ext in SUPPORTED_DOCS:
            return await self._process_pdf_bytes(data, filename)

        if ext in SUPPORTED_TEXT:
            try:
                text = data.decode("utf-8", errors="replace")
                return [{"type": "text", "text": f"📄 Arquivo de texto **{filename}**:\n{text[:4000]}"}]
            except Exception as exc:
                return [{"type": "text", "text": f"⚠️ Não foi possível ler **{filename}**: {exc}"}]

        if ext in SUPPORTED_AUDIO:
            return await self._process_audio_bytes(data, filename)

        if ext in SUPPORTED_VECTOR:
            return await self._process_vector_bytes(data, filename)

        if ext in SUPPORTED_EDITABLE:
            return await self._process_psd_bytes(data, filename)

        return [{"type": "text", "text": f"📎 Arquivo recebido: **{filename}** (formato {ext or 'desconhecido'} — não processável visualmente)"}]

    async def _process_vector(self, attachment) -> list[dict]:
        """Baixa um arquivo vetorial (.ps, .eps, .ai) e renderiza via Ghostscript."""
        try:
            data = await self._download(attachment.url)
            return await self._process_vector_bytes(data, attachment.filename)
        except Exception as exc:
            logger.error(f"Erro ao baixar vetorial {attachment.filename}: {exc}")
            return [{"type": "text", "text": f"⚠️ Não foi possível baixar **{attachment.filename}**: {exc}"}]

    async def _process_vector_bytes(self, data: bytes, filename: str) -> list[dict]:
        """
        Converte PS/EPS/AI para PNG via Ghostscript e retorna como image_url base64.
        Fallback: nota de texto indicando que é um arquivo vetorial válido.
        """
        import shutil
        import asyncio
        import tempfile
        import os

        gs_path = shutil.which("gs")
        if not gs_path:
            logger.warning("Ghostscript (gs) não encontrado — vetorial sem renderização")
            return [
                {
                    "type": "text",
                    "text": (
                        f"🎨 Arquivo vetorial **{filename}** (PostScript/EPS/AI) — "
                        f"vetor — escalável sem perda. Formato válido para produção offset. "
                        f"Ghostscript não disponível; análise visual não realizada."
                    ),
                }
            ]

        try:
            from PIL import Image as PILImage

            with tempfile.TemporaryDirectory() as tmpdir:
                input_path  = os.path.join(tmpdir, filename)
                output_path = os.path.join(tmpdir, "page_%03d.png")

                with open(input_path, "wb") as f:
                    f.write(data)

                # Chama Ghostscript de forma assíncrona
                proc = await asyncio.create_subprocess_exec(
                    gs_path,
                    "-dNOPAUSE", "-dBATCH", "-dSAFER",
                    "-sDEVICE=png16m",
                    "-r150",
                    f"-sOutputFile={output_path}",
                    input_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(proc.wait(), timeout=30)

                # Coleta páginas geradas (máx. pdf_max_pages)
                pages = sorted(
                    f for f in os.listdir(tmpdir) if f.startswith("page_") and f.endswith(".png")
                )[: self.pdf_max_pages]

                if not pages:
                    raise RuntimeError(f"Ghostscript não gerou nenhuma página. Stderr: {stderr}")

                parts: list[dict] = [
                    {
                        "type": "text",
                        "text": f"🎨 Arquivo vetorial **{filename}** — vetor — escalável sem perda — {len(pages)} página(s) renderizada(s):",
                    }
                ]

                for page_name in pages:
                    img = PILImage.open(os.path.join(tmpdir, page_name))

                    # Normaliza modo e redimensiona
                    if img.mode in ("RGBA", "LA", "P"):
                        img = img.convert("RGBA")
                        fmt, mime = "PNG", "png"
                    else:
                        img = img.convert("RGB")
                        fmt, mime = "JPEG", "jpeg"

                    MAX_PX = 1568
                    if max(img.size) > MAX_PX:
                        img.thumbnail((MAX_PX, MAX_PX), PILImage.LANCZOS)

                    buf = io.BytesIO()
                    img.save(buf, format=fmt, quality=90)
                    b64 = base64.b64encode(buf.getvalue()).decode()

                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{mime};base64,{b64}",
                                "detail": "high",
                            },
                        }
                    )

                logger.info(f"Vetorial OK: {filename} → {len(pages)} página(s) via Ghostscript")
                return parts

        except asyncio.TimeoutError:
            logger.error(f"Ghostscript demorou demais para converter {filename}")
            return [
                {
                    "type": "text",
                    "text": (
                        f"🎨 Arquivo vetorial **{filename}** (PS/EPS/AI — vetor — escalável sem perda). "
                        f"Renderização não concluída a tempo — verifique o arquivo manualmente."
                    ),
                }
            ]
        except Exception as exc:
            logger.error(f"Erro ao renderizar vetorial {filename}: {exc}", exc_info=True)
            return [
                {
                    "type": "text",
                    "text": (
                        f"🎨 Arquivo vetorial **{filename}** (PS/EPS/AI — vetor — escalável sem perda). "
                        f"Não foi possível renderizar: {exc}"
                    ),
                }
            ]

    async def _process_audio(self, attachment) -> list[dict]:
        """Baixa um arquivo de áudio do Discord e transcreve via Whisper."""
        try:
            data = await self._download(attachment.url)
            return await self._process_audio_bytes(data, attachment.filename)
        except Exception as exc:
            logger.error(f"Erro ao baixar áudio {attachment.filename}: {exc}")
            return [{"type": "text", "text": f"⚠️ Não foi possível baixar o áudio **{attachment.filename}**: {exc}"}]

    async def _process_audio_bytes(self, data: bytes, filename: str) -> list[dict]:
        """Transcreve bytes de áudio via Whisper-1 e retorna como parte de texto."""
        try:
            buf = io.BytesIO(data)
            buf.name = filename  # extensão necessária para o Whisper identificar o formato

            client = self._get_openai()
            transcript = await client.audio.transcriptions.create(
                model="whisper-1",
                file=buf,
                language="pt",
            )
            text = transcript.text.strip()
            logger.info(f"Áudio transcrito: {filename} → {len(text)} chars")
            return [
                {
                    "type": "text",
                    "text": (
                        f"🎙️ Áudio **{filename}** — transcrição:\n\n{text}"
                    ),
                }
            ]
        except Exception as exc:
            logger.error(f"Erro ao transcrever áudio {filename}: {exc}", exc_info=True)
            return [{"type": "text", "text": f"⚠️ Não foi possível transcrever o áudio **{filename}**: {exc}"}]

    # ── processamento por bytes (reutilizável internamente) ───────────────────

    async def _process_image_bytes(self, data: bytes, filename: str) -> list[dict]:
        """Valida, redimensiona e converte bytes de imagem para base64."""
        try:
            from PIL import Image as PILImage

            size_kb = len(data) / 1024
            img = PILImage.open(io.BytesIO(data))

            # Capturar metadados ANTES de qualquer conversão (CY7.1)
            formato_real = img.format or "DESCONHECIDO"
            orig_w, orig_h = img.size

            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                fmt, mime = "PNG", "png"
                tem_alpha = img.getextrema()[3][0] < 255
            else:
                img = img.convert("RGB")
                fmt, mime = "JPEG", "jpeg"
                tem_alpha = False

            MAX_PX = 1568
            if max(img.size) > MAX_PX:
                img.thumbnail((MAX_PX, MAX_PX), PILImage.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format=fmt, quality=90)
            b64 = base64.b64encode(buf.getvalue()).decode()

            logger.info(f"Imagem OK: {filename} → {img.size} {fmt} ({len(b64)//1024}KB b64)")

            meta_text = (
                f"[DADOS TÉCNICOS {filename}: formato {formato_real}, "
                f"{orig_w}×{orig_h} px (original), "
                f"fundo transparente: {'sim' if tem_alpha else 'não'}, "
                f"{size_kb:.0f} KB]"
            )
            return [
                {"type": "text", "text": meta_text},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}", "detail": "high"}},
            ]
        except Exception as exc:
            logger.error(f"Erro ao processar imagem {filename}: {exc}", exc_info=True)
            return [{"type": "text", "text": f"⚠️ Não foi possível processar a imagem **{filename}**: {exc}"}]

    async def _process_pdf_bytes(self, data: bytes, filename: str) -> list[dict]:
        """Converte bytes de PDF em imagens via pdf2image."""
        try:
            import shutil
            from pdf2image import convert_from_bytes  # type: ignore

            pdftoppm = shutil.which("pdftoppm")
            poppler_path = str(__import__("pathlib").Path(pdftoppm).parent) if pdftoppm else "/usr/bin"

            images = convert_from_bytes(
                data,
                dpi=self.pdf_dpi,
                first_page=1,
                last_page=self.pdf_max_pages,
                poppler_path=poppler_path,
            )

            parts: list[dict] = [{"type": "text", "text": f"📄 PDF **{filename}** — primeiras {len(images)} página(s):"}]

            for img in images:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}})

            return parts

        except ImportError:
            logger.warning("pdf2image não instalado — usando extração de texto como fallback")
            return await self._pdf_text_fallback_bytes(data, filename)

        except Exception as exc:
            logger.error(f"Erro ao converter PDF {filename}: {exc}", exc_info=True)
            return [{"type": "text", "text": f"⚠️ Não foi possível processar o PDF **{filename}**: {exc}"}]

    async def _pdf_text_fallback_bytes(self, data: bytes, filename: str) -> list[dict]:
        """Extrai texto do PDF usando pypdf quando pdf2image não está disponível."""
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[: self.pdf_max_pages])
            return [{"type": "text", "text": f"📄 PDF **{filename}** (texto extraído):\n" + text[:3000]}]

        except Exception as exc:
            logger.error(f"Erro ao extrair texto do PDF {filename}: {exc}")
            return [{"type": "text", "text": f"⚠️ PDF **{filename}** recebido mas não processável: {exc}"}]

    async def _process_psd(self, url: str, filename: str) -> list[dict]:
        """Baixa um PSD e renderiza via psd-tools."""
        try:
            data = await self._download(url)
        except Exception as exc:
            logger.error(f"Erro ao baixar PSD {filename}: {exc}")
            return [{"type": "text", "text": f"⚠️ Não foi possível baixar **{filename}**: {exc}"}]
        return await self._process_psd_bytes(data, filename)

    async def _process_psd_bytes(self, data: bytes, filename: str) -> list[dict]:
        """Renderiza bytes de PSD flat via psd-tools e retorna como image_url base64."""
        try:
            from psd_tools import PSDImage
            from PIL import Image as PILImage

            psd = PSDImage.open(io.BytesIO(data))
            img = psd.composite()

            if img is None:
                raise RuntimeError("psd-tools retornou imagem vazia")

            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                fmt, mime = "PNG", "png"
                tem_alpha = img.getextrema()[3][0] < 255
            else:
                img = img.convert("RGB")
                fmt, mime = "JPEG", "jpeg"
                tem_alpha = False

            MAX_PX = 1568
            if max(img.size) > MAX_PX:
                img.thumbnail((MAX_PX, MAX_PX), PILImage.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format=fmt, quality=90)
            b64 = base64.b64encode(buf.getvalue()).decode()

            logger.info(f"PSD renderizado: {filename} → {img.size} {fmt} ({len(b64)//1024}KB b64)")

            return [
                {"type": "text", "text": f"🎨 PSD renderizado: **{filename}** ({psd.width}×{psd.height}px, {len(psd)} camada(s), fundo transparente: {'sim' if tem_alpha else 'não'})"},
                {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}", "detail": "high"}},
            ]

        except Exception as exc:
            logger.error(f"Erro ao renderizar PSD {filename}: {exc}", exc_info=True)
            return [{"type": "text", "text": f"📎 PSD recebido: **{filename}** — não foi possível renderizar. Será verificado pelo setor de Arte."}]

    @staticmethod
    async def _download(url: str) -> bytes:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; CyanBot/1.0)"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, allow_redirects=True) as resp:
                resp.raise_for_status()
                return await resp.read()
