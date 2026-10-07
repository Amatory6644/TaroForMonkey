import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from telok import storage
from telok.db import transaction, uid
from telok.domain import DomainError, owned, row_dict
from telok.editorial import release
from telok.models import Item, Version, Work
from telok.schemas import Storyboard
from telok.settings import settings


def run(args: list[str], timeout=120):
    result = subprocess.run(
        args, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    if result.returncode:
        raise DomainError("Media-команда завершилась ошибкой. Проверьте поддерживаемые файлы и FFmpeg.")
    return result.stdout


def windows_voice(text: str, target: Path, locale: str):

    if os.name != "nt":
        raise DomainError("Windows voice недоступен на Linux: загрузите narration audio asset.")
    text_path = target.with_suffix(".txt")
    text_path.write_text(text, encoding="utf-8")
    script = Path(__file__).parent.parent / "scripts" / "voice.ps1"
    run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-TextPath",
            str(text_path),
            "-OutputPath",
            str(target),
            "-Locale",
            locale,
        ],
        timeout=60,
    )


def render_release(project_id: str, actor: int, payload: dict):
    request_id = payload.get("request_id") or uid()
    with transaction() as session:
        previous = session.execute(
            select(Version).where(Version.request_id == request_id)
        ).scalar_one_or_none()
        if previous:
            return row_dict(previous)
    storyboard = Storyboard.model_validate(payload["storyboard"])
    total = sum(scene.duration for scene in storyboard.scenes)
    if total > 120:
        raise DomainError("VIDEO_SHORT ограничен 120 секундами.")
    with transaction() as session:
        project = owned(session, project_id, actor)
        if project.paused:
            raise DomainError("Проект на паузе.")
        brand_revision, brand_name = project.brand_revision, project.name
    cfg = settings()
    ffmpeg = shutil.which(cfg.ffmpeg) or cfg.ffmpeg
    ffprobe = shutil.which(cfg.ffprobe) or cfg.ffprobe
    narration = " ".join(scene.narration for scene in storyboard.scenes)
    if not narration.strip():
        raise DomainError("Нужна narration для видео.")
    with transaction() as session:
        previous = session.get(Version, payload.get("previous_video_id", ""))
        demo = bool(previous and previous.demo)
    with tempfile.TemporaryDirectory(prefix="telok-render-") as temp:
        directory = Path(temp)
        clips = []
        for index, scene in enumerate(storyboard.scenes):
            data, meta = storage.read(scene.image_asset_id, project_id)
            demo = demo or bool(meta["info"].get("demo"))
            with Image.open(io.BytesIO(data)) as source:
                source = source.convert("RGB")
                from PIL import ImageOps

                frame = Image.new("RGB", (720, 1280), source.getpixel((0, 0)))
                visual = ImageOps.contain(source, (650, 760))
                frame.paste(visual, ((720 - visual.width) // 2, 160 + (760 - visual.height) // 2))
            draw = ImageDraw.Draw(frame)
            draw.rounded_rectangle((35, 1010, 685, 1245), radius=28, fill="#17231f")
            font_paths = [
                Path("C:/Windows/Fonts/arial.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ]
            font_path = next((str(p) for p in font_paths if p.exists()), None)
            font = ImageFont.truetype(font_path, 32) if font_path else ImageFont.load_default(size=32)
            draw.text((40, 60), brand_name[:35], fill="#17231f", font=font)
            words, lines, line = scene.caption[:180].split(), [], ""
            for word in words:
                candidate = (line + " " + word).strip()
                if draw.textlength(candidate, font=font) > 570 and line:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            lines.append(line)
            if len(lines) > 4 or any(draw.textlength(line, font=font) > 570 for line in lines):
                raise DomainError("Caption не помещается в safe area. Сократите текст.")
            draw.multiline_text((70, 1045), "\n".join(lines[:4]), fill="#ffffff", font=font, spacing=12)
            frame_path = directory / f"frame-{index}.png"
            frame.save(frame_path)
            clip = directory / f"clip-{index}.mp4"
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(frame_path),
                    "-t",
                    str(scene.duration),
                    "-vf",
                    "zoompan=z='min(zoom+0.0007,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=720x1280:fps=24",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-threads",
                    "2",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    str(clip),
                ]
            )
            clips.append(clip)
        voice = directory / "voice.wav"
        if payload.get("audio_asset_id"):
            data, meta = storage.read(payload["audio_asset_id"], project_id)
            if not meta["mime"].startswith("audio/"):
                raise DomainError("Narration asset должен быть аудио.")
            voice.write_bytes(data)
        elif payload.get("tts") == "windows":
            audio_clips = []
            for index, scene in enumerate(storyboard.scenes):
                if not scene.narration.strip():
                    raise DomainError("Для каждой TTS-сцены нужна narration.")
                scene_voice = directory / f"voice-{index}.wav"
                windows_voice(scene.narration, scene_voice, payload.get("locale", "ru-RU"))
                scene_probe = json.loads(
                    run([ffprobe, "-v", "error", "-show_format", "-of", "json", str(scene_voice)])
                )
                if float(scene_probe["format"]["duration"]) > scene.duration + 0.1:
                    raise DomainError(f"Речь сцены {index + 1} длиннее сцены. Увеличьте duration.")
                padded = directory / f"audio-{index}.wav"
                run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-y",
                        "-i",
                        str(scene_voice),
                        "-af",
                        "apad",
                        "-t",
                        str(scene.duration),
                        "-c:a",
                        "pcm_s16le",
                        "-ar",
                        "48000",
                        "-ac",
                        "1",
                        str(padded),
                    ]
                )
                audio_clips.append(padded)
            audio_inputs = directory / "audio-inputs.txt"
            audio_inputs.write_text(
                "\n".join(f"file '{p.as_posix()}'" for p in audio_clips), encoding="utf-8"
            )
            run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(audio_inputs),
                    "-c:a",
                    "pcm_s16le",
                    str(voice),
                ]
            )
        else:
            raise DomainError("Загрузите narration audio либо выберите доступный локальный TTS.")
        audio_probe = json.loads(run([ffprobe, "-v", "error", "-show_format", "-of", "json", str(voice)]))
        if float(audio_probe["format"]["duration"]) > total + 0.1:
            raise DomainError(
                "Narration длиннее timeline: увеличьте длительность сцен; речь не будет обрезана."
            )
        inputs = directory / "inputs.txt"
        inputs.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
        output = directory / "final.mp4"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(inputs),
                "-i",
                str(voice),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-af",
                "apad",
                "-t",
                str(total),
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        probe = json.loads(
            run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)])
        )
        streams = probe["streams"]
        has_video = any(
            s["codec_type"] == "video" and s.get("width") == 720 and s.get("height") == 1280 for s in streams
        )
        has_audio = any(s["codec_type"] == "audio" for s in streams)
        duration = float(probe["format"]["duration"])
        if not has_video or not has_audio or abs(duration - total) > 0.25:
            raise DomainError("Конечный MP4 не прошёл техническую проверку.")
        poster = directory / "poster.png"
        run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                "0.25",
                "-i",
                str(output),
                "-frames:v",
                "1",
                str(poster),
            ]
        )
        poster_id = storage.put(
            project_id,
            poster.read_bytes(),
            "image/png",
            {
                "demo": demo,
                "role": "video_preview",
                "source_video_hash": hashlib.sha256(output.read_bytes()).hexdigest(),
            },
        )
        asset_id = storage.put(
            project_id,
            output.read_bytes(),
            "video/mp4",
            {
                "renderer": "ffmpeg-motion-contain-v2",
                "poster_asset_id": poster_id,
                "duration": duration,
                "width": 720,
                "height": 1280,
                "demo": demo,
                "storyboard": storyboard.model_dump(),
                "audio_review": "owner_required",
                "audio_alignment": "per_scene_tts"
                if payload.get("tts") == "windows" and not payload.get("audio_asset_id")
                else "owner_provided_timeline",
                "audio_source": payload.get("audio_asset_id")
                or ("windows:" + payload.get("locale", "ru-RU")),
            },
        )
    qa = {
        "hard": [],
        "findings": [
            {
                "category": "editorial",
                "message": "Прослушайте ролик: совпадение речи, captions и timeline ещё не проверено моделью.",
                "fix": "Просмотреть полный ролик перед подтверждением.",
            }
        ],
        "summary": "MP4: video/audio streams, размеры и duration проверены; редакторская приёмка требуется.",
    }
    with transaction() as session:
        project = owned(session, project_id, actor)
        work = session.get(Work, request_id)
        if work and work.status == "CANCELLED":
            raise DomainError("Видеозадача отменена до выпуска.")
        if project.paused or project.brand_revision != brand_revision:
            raise DomainError("Проект остановлен либо бренд изменился во время render.")
        if payload.get("item_id"):
            item = session.execute(
                select(Item).where(Item.id == payload["item_id"]).with_for_update()
            ).scalar_one()
            if item.revision != payload["revision"]:
                raise DomainError("Видеорезультат устарел.")
        else:
            item = Item(project_id=project_id, revision=1)
            session.add(item)
            session.flush()
        version = release(
            session,
            project,
            item,
            request_id,
            storyboard.title,
            [asset_id],
            {
                "snapshot": {"brand_revision": brand_revision},
                "storyboard": storyboard.model_dump(),
                "poster_asset_id": poster_id,
                "audio_asset_id": payload.get("audio_asset_id"),
                "tts": payload.get("tts"),
                "locale": payload.get("locale", "ru-RU"),
            },
            qa,
            demo=demo,
            format_name="VIDEO_SHORT",
        )
        if work:
            work.status, work.result = version.status, {"version_id": version.id}
        return row_dict(version)
