"""Golden question datasets (blueprint §12).

A starter set aligned with the demo corpora. Questions are seeded into the
`golden_questions` table when a matching dataset is empty, and can be extended
via the API at any time.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GoldenQuestion
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_QUESTIONS: dict[str, list[dict]] = {
    "tech": [
        {
            "question": "Who created FastAPI?",
            "ground_truth": "Sebastián Ramírez created FastAPI in 2018.",
            "source_hint": "fastapi-notes",
        },
        {
            "question": "What does FastAPI generate automatically for every endpoint?",
            "ground_truth": "FastAPI automatically generates OpenAPI documentation, including Swagger UI at /docs and ReDoc at /redoc.",
            "source_hint": "fastapi-notes",
        },
        {
            "question": "Which libraries is FastAPI built on?",
            "ground_truth": "FastAPI is built on Starlette for the web parts and Pydantic for the data parts.",
            "source_hint": "fastapi-notes",
        },
        {
            "question": "What do Pydantic models power in FastAPI?",
            "ground_truth": "Pydantic models power request and response validation, with automatic JSON schema generation.",
            "source_hint": "fastapi-notes",
        },
    ],
    "arts": [
        {
            "question": "Which movement is associated with Impressionist paintings of water?",
            "ground_truth": "Impressionism, exemplified by artists such as Monet and Renoir, is associated with paintings of water and light.",
            "source_hint": "art-history",
        },
        {
            "question": "Who is credited with founding the modern novel in English literature?",
            "ground_truth": "Authors such as Defoe and Richardson are credited with the rise of the modern English novel in the 18th century.",
            "source_hint": "gutenberg",
        },
        {
            "question": "What is the setting of Shakespeare's Hamlet?",
            "ground_truth": "Hamlet is set in the kingdom of Denmark, principally at Elsinore castle.",
            "source_hint": "gutenberg",
        },
    ],
}


async def ensure_dataset_seeded(session: AsyncSession, dataset_id: str) -> int:
    """Seed default golden questions for a dataset when it has none. Returns count added."""
    rows = (
        await session.execute(select(func.count(GoldenQuestion.id)).where(GoldenQuestion.corpus == dataset_id))
    ).scalar_one()
    if rows:
        return 0
    questions = DEFAULT_QUESTIONS.get(dataset_id, [])
    if not questions:
        return 0
    session.add_all([GoldenQuestion(corpus=dataset_id, **q) for q in questions])
    await session.commit()
    logger.info("seeded %d golden questions for dataset %s", len(questions), dataset_id)
    return len(questions)


async def load_questions(session: AsyncSession, dataset_id: str) -> list[GoldenQuestion]:
    stmt = select(GoldenQuestion).where(GoldenQuestion.corpus == dataset_id).order_by(GoldenQuestion.id)
    return list((await session.execute(stmt)).scalars().all())
