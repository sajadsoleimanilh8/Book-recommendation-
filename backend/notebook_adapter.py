from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class NotebookAdapter:
    notebook_path: Path
    _cached_recommend_fn: Optional[Callable[..., Any]] = field(default=None, init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def _load_recommend_function(self) -> Optional[Callable[..., Any]]:
        if self._loaded:
            return self._cached_recommend_fn

        if not self.notebook_path.exists():
            self._loaded = True
            return None

        try:
            with self.notebook_path.open("r", encoding="utf-8") as f:
                nb = json.load(f)
        except Exception:
            self._loaded = True
            return None

        namespace: Dict[str, Any] = {}
        cells = nb.get("cells", [])

        for cell in cells:
            if cell.get("cell_type") != "code":
                continue
            source = "".join(cell.get("source", []))
            if "def " not in source:
                continue
            try:
                tree = ast.parse(source)
            except Exception:
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    mod = ast.Module(body=[node], type_ignores=[])
                    code = compile(mod, filename=str(self.notebook_path), mode="exec")
                    try:
                        exec(code, namespace, namespace)
                    except Exception:
                        continue

        fn = namespace.get("recommend_books")
        if callable(fn):
            self._cached_recommend_fn = fn

        self._loaded = True
        return self._cached_recommend_fn

    def recommend(
        self,
        *,
        payload: Any,
        books: List[Dict[str, Any]],
        fallback: Callable[[Any, int], List[Dict[str, Any]]],
        top_k: int = 12,
    ) -> List[Dict[str, Any]]:
        rec_fn = self._load_recommend_function()
        if rec_fn is None:
            return fallback(payload, top_k)

        try:
            import pandas as pd
        except Exception:
            return fallback(payload, top_k)

        try:
            rows = []
            for b in books:
                rows.append(
                    {
                        "book_id": b.get("book_id") or b.get("id"),
                        "cluster": b.get("cluster", 0),
                        "title": b.get("title"),
                        "author": b.get("author"),
                        "genre": b.get("genre"),
                        "average_rating": b.get("rating", 0),
                        "list_price": b.get("price", 0),
                    }
                )
            df = pd.DataFrame(rows)
            if df.empty:
                return fallback(payload, top_k)

            seed_book_id = None
            favorite_book = getattr(payload, "favorite_book", None)
            if favorite_book:
                matches = df[df["title"].astype(str).str.lower().str.contains(str(favorite_book).lower(), na=False)]
                if not matches.empty:
                    seed_book_id = matches.iloc[0]["book_id"]

            if seed_book_id is None:
                genre = getattr(payload, "genre", None)
                sub = df
                if genre:
                    sub = df[df["genre"].astype(str).str.lower().str.contains(str(genre).lower(), na=False)]
                if sub.empty:
                    sub = df
                sub = sub.sort_values(by=["average_rating"], ascending=False)
                seed_book_id = sub.iloc[0]["book_id"]

            recs = rec_fn(seed_book_id, df, n_recommend=top_k)
            if recs is None:
                return fallback(payload, top_k)

            if hasattr(recs, "to_dict"):
                rec_rows = recs.to_dict(orient="records")
            else:
                rec_rows = []

            by_title = {str(b.get("title", "")).strip().lower(): b for b in books}
            out: List[Dict[str, Any]] = []
            for r in rec_rows:
                title_key = str(r.get("title", "")).strip().lower()
                if title_key in by_title:
                    out.append(by_title[title_key])

            if out:
                return out[:top_k]
        except Exception:
            return fallback(payload, top_k)

        return fallback(payload, top_k)
