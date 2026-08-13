"""Golden question datasets (blueprint §12).

A starter set aligned with the demo corpora. Questions are seeded into the
`golden_questions` table when a matching dataset is empty, and can be extended
via the API at any time.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models import GoldenQuestion

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
            "ground_truth": (
                "FastAPI automatically generates OpenAPI documentation, including Swagger UI "
                "at /docs and ReDoc at /redoc."
            ),
            "source_hint": "fastapi-notes",
        },
        {
            "question": "Which libraries is FastAPI built on?",
            "ground_truth": (
                "FastAPI is built on Starlette for the web parts and Pydantic for the data parts."
            ),
            "source_hint": "fastapi-notes",
        },
        {
            "question": "What do Pydantic models power in FastAPI?",
            "ground_truth": (
                "Pydantic models power request and response validation, with automatic JSON "
                "schema generation."
            ),
            "source_hint": "fastapi-notes",
        },
        # P3.3: metadata-scoped question (exercises tags/date/author filters).
        {
            "question": (
                "What does the 2024 documentation say about FastAPI's dependency injection?"
            ),
            "ground_truth": (
                "FastAPI's dependency injection lets functions declare dependencies in their "
                "signature, which FastAPI solves and injects automatically, including "
                "sub-dependencies and yield-based cleanup."
            ),
            "source_hint": "fastapi-notes",
        },
        # P3.3: cross-section question (how one area relates to another).
        {
            "question": "How does FastAPI's routing relate to its request validation?",
            "ground_truth": (
                "Routing and validation work together: each route declares a path operation "
                "whose parameters and body are validated against the declared models before "
                "the handler runs."
            ),
            "source_hint": "fastapi-notes",
        },
    ],
    "arts": [
        {
            "question": "Which movement is associated with Impressionist paintings of water?",
            "ground_truth": (
                "Impressionism, exemplified by artists such as Monet and Renoir, is associated "
                "with paintings of water and light."
            ),
            "source_hint": "art-history",
        },
        {
            "question": "Who is credited with founding the modern novel in English literature?",
            "ground_truth": (
                "Authors such as Defoe and Richardson are credited with the rise of the modern "
                "English novel in the 18th century."
            ),
            "source_hint": "gutenberg",
        },
        {
            "question": "What is the setting of Shakespeare's Hamlet?",
            "ground_truth": (
                "Hamlet is set in the kingdom of Denmark, principally at Elsinore castle."
            ),
            "source_hint": "gutenberg",
        },
    ],
    # Grounded in the demo philosophy corpus (demo/philosophy/*.txt) — the corpus name
    # matches the vault it was ingested into so matrix runs retrieve real chunks.
    "Philosophy": [
        {
            "question": (
                "What did Descartes claim was so certain that he adopted it as the first "
                "principle of his philosophy?"
            ),
            "ground_truth": (
                "Descartes stated that 'I think, therefore I am' (COGITO ERGO SUM) was so "
                "certain that he adopted it as the first principle of the philosophy he was "
                "seeking."
            ),
            "source_hint": "descartes-discourse",
        },
        {
            "question": (
                "Per John Stuart Mill, what is the only purpose for which power can be "
                "rightfully exercised over a member of a civilized community against their will?"
            ),
            "ground_truth": (
                "Mill's harm principle holds that the only purpose for which power can be "
                "rightfully exercised over any member of a civilized community, against their "
                "will, is to prevent harm to others."
            ),
            "source_hint": "mill-on-liberty",
        },
        {
            "question": "How does Aristotle characterize virtue in the Nicomachean Ethics?",
            "ground_truth": (
                "Aristotle characterizes moral virtue as a mean state between the extremes of "
                "excess and defect, a matter of finding and adopting the mean in actions and "
                "feelings."
            ),
            "source_hint": "aristotle-nicomachean-ethics",
        },
        {
            "question": (
                "In Kant's Critique of Pure Reason, how are judgements classified, and what "
                "relation do they bear to experience?"
            ),
            "ground_truth": (
                "Kant distinguishes analytical from synthetical (synthetic) judgements, and "
                "argues that synthetic a priori judgements are possible in the theoretical "
                "sciences of reason."
            ),
            "source_hint": "kant-critique-of-pure-reason",
        },
        # P3.3: metadata-scoped question (exercises date filters on the corpus).
        {
            "question": (
                "What does Mill's On Liberty, published in 1859, say about freedom of opinion?"
            ),
            "ground_truth": (
                "Mill argues that silencing an opinion robs the human race of the truth or "
                "of the benefit of error corrected, so freedom of opinion is essential to "
                "liberty."
            ),
            "source_hint": "mill-on-liberty",
        },
        # P3.3: cross-section question (how one doctrine relates to another).
        {
            "question": (
                "How does Plato's allegory of the cave relate to Descartes' method of doubt?"
            ),
            "ground_truth": (
                "Both use a radical break from the world of appearances: Plato's prisoners "
                "must leave the cave's shadows to reach the Forms, and Descartes must doubt "
                "all that can be doubted before accepting what is certain."
            ),
            "source_hint": "plato-republic",
        },
        {
            "question": (
                "What does the allegory of the cave in Plato's Republic use the cave and its "
                "fire to represent?"
            ),
            "ground_truth": (
                "In the allegory of the cave (Book VII of the Republic), the cave is the world "
                "of sight, the fire is the source of light by which the prisoners see only "
                "shadows, and the ascent out of the cave is the journey into the intellectual "
                "world."
            ),
            "source_hint": "plato-republic",
        },
    ],
}


async def ensure_dataset_seeded(session: AsyncSession, dataset_id: str) -> int:
    """Seed default golden questions for a dataset when it has none. Returns count added."""
    rows = (
        await session.execute(
            select(func.count(GoldenQuestion.id)).where(GoldenQuestion.corpus == dataset_id)
        )
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
    stmt = (
        select(GoldenQuestion)
        .where(GoldenQuestion.corpus == dataset_id)
        .order_by(GoldenQuestion.id)
    )
    return list((await session.execute(stmt)).scalars().all())
