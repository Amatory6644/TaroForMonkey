import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select

from telok import storage
from telok.db import transaction, uid
from telok.domain import calendar_slots, create_project
from telok.editorial import release
from telok.models import Item, Plan, Project, Version
from telok.settings import settings


def poster(title: str = "A little room\nfor a big idea.", variant: int = 0) -> bytes:
    palettes = [
        ("#e8efdf", "#152c21", "#a0bc8b"),
        ("#eee8de", "#3c2b24", "#d3b8a3"),
        ("#e2e8f3", "#203352", "#a4bad9"),
    ]
    bg, ink, accent = palettes[variant % 3]
    image = Image.new("RGB", (1024, 1024), bg)
    draw = ImageDraw.Draw(image)
    fonts = [Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    font_path = next((str(p) for p in fonts if p.exists()), None)

    def font(size):
        return ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size=size)

    draw.ellipse((620, -180, 1260, 460), fill=accent)
    draw.rounded_rectangle((690, 640, 1120, 1080), radius=100, fill=accent)
    draw.text((76, 70), "telok", font=font(48), fill=ink)
    draw.line((76, 165, 948, 165), fill=ink, width=2)
    draw.text((76, 220), "A NEW CHAPTER", font=font(18), fill=ink)
    draw.multiline_text((70, 360), title, font=font(90), fill=ink, spacing=12)
    draw.text((76, 872), "EDITORIAL STUDY  /  001", font=font(20), fill=ink)
    draw.text((76, 920), "Design sample - not AI-generated", font=font(18), fill=ink)
    result = io.BytesIO()
    image.save(result, format="PNG")
    return result.getvalue()


def seed():
    with transaction() as session:
        project = session.execute(
            select(Project).where(Project.owner_id == settings().owner_id, Project.name == "Telok")
        ).scalar_one_or_none()
    if not project:
        project_dict = create_project(settings().owner_id, "Telok", "")
        project_id = project_dict["id"]
    else:
        project_id = project.id
    with transaction() as session:
        if session.execute(
            select(Version).where(Version.project_id == project_id, Version.demo.is_(True))
        ).first():
            return project_id
    captions = [
        "Начнём с вопроса.\n\nКакую задачу вам хотелось бы решить проще?\nПоделитесь одной мыслью — мы внимательно прочитаем.",
        "Хорошая идея иногда начинается с паузы.\n\nЧто помогло вам посмотреть на привычную задачу по-новому?",
        "Оставим место для следующей идеи.\n\nВыберите тему, которую вам было бы интересно обсудить.",
    ]
    titles = ["A little room\nfor a big idea.", "Pause.\nThen begin.", "What comes\nnext?"]
    for index, caption in enumerate(captions):
        asset_id = storage.put(
            project_id,
            poster(titles[index], index),
            "image/png",
            {"provider": "local-design-fixture", "demo": True},
        )
        with transaction() as session:
            project = session.get(Project, project_id)
            item = Item(project_id=project_id, revision=1)
            session.add(item)
            session.flush()
            release(
                session,
                project,
                item,
                uid(),
                caption,
                [asset_id],
                {"snapshot": {"brand_revision": project.brand_revision}, "brief": "Демонстрационный дизайн"},
                {"hard": [], "findings": [], "summary": "Дизайн-пример. Не является live API pilot."},
                demo=True,
            )
    with transaction() as session:
        session.add(
            Plan(
                project_id=project_id,
                slots=calendar_slots(
                    [
                        {
                            "title": "Вопрос аудитории",
                            "hook": "Один вопрос",
                            "goal": "Обсуждение",
                            "format": "IMAGE_POST",
                        },
                        {
                            "title": "История одной идеи",
                            "hook": "Начнём с наблюдения",
                            "goal": "Знакомство",
                            "format": "TEXT_POST",
                        },
                        {
                            "title": "Выбор следующей темы",
                            "hook": "Что обсудим?",
                            "goal": "Обратная связь",
                            "format": "IMAGE_POST",
                        },
                    ],
                    "2026-10-05",
                    "Europe/Moscow",
                ),
            )
        )
    return project_id
