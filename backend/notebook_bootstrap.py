from __future__ import annotations

from pathlib import Path
from typing import Any

from .notebook_adapter import NotebookAdapter


def patch_notebook_recommender(main_module: Any) -> None:
    notebook_path = Path(__file__).parent / "1v1 final.ipynb"
    adapter = NotebookAdapter(notebook_path=notebook_path)
    fallback = main_module.notebook_style_recommend

    def _wrapped(payload: Any, top_k: int = 12):
        return adapter.recommend(payload=payload, books=main_module.BOOKS, fallback=fallback, top_k=top_k)

    main_module.notebook_style_recommend = _wrapped
