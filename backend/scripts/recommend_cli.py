"""Interactive terminal questionnaire — a manual smoke test, not app code.

    python -m scripts.recommend_cli

Loads a catalogue from the same CSV fallback chain the competition
baseline used, fits a Recommender, and drops into QuestionerEngine's
input() loop until the user quits.

Extracted verbatim from the `if __name__ == "__main__":` block that used
to close out `engine.py` (RESTRUCTURE-NOTES B-2 — a facade of ≤40 lines
could not carry a 40-line CLI, and it is a CLI rather than app code, not
a bug, so it moves here rather than being deleted). Behaviour unchanged;
only the invocation path changes, from `python engine.py` to
`python -m scripts.recommend_cli`.

Note the CSV filenames below are the same four `main.py`'s
`CSV_FALLBACKS` names and are equally hypothetical — none has ever
existed in this repository (see F-49 in PROGRESS.md). This script has
always fallen straight through to `DataLoader._synthetic()` in practice;
recorded here rather than fixed, per this restructure's no-behaviour-
change rule.
"""

from ml.data_loader import DataLoader
from services.recommendation import QuestionerEngine, Recommender

if __name__ == "__main__":
    print("\n Loading dataset and fitting ML engine…")
    
    try:
        _df = DataLoader.load([
            "merged_complete_dataset.csv",
            "google_books_dataset.csv",
            "dataset_gutenberg.csv",
            "bookg.csv",
        ])
    except Exception as e:
        print(f"Warning: Could not load CSV files: {e}")
        _df = DataLoader._synthetic()

    if _df is not None:
        try:
            _rec = Recommender(_df)
            _rec.fit()

            qe = QuestionerEngine(_rec)

            while True:
                try:
                    qe.run(n=10)
                    again = input(" Search again? [Enter = yes / n = quit] ").strip().lower()
                    if again == "n":
                        print("\n Thanks for using DigiKitab. Happy reading!\n")
                        break
                except KeyboardInterrupt:
                    print("\n\n Operation cancelled by user.")
                    break
                except Exception as e:
                    print(f"\n An error occurred: {e}")
                    import traceback
                    traceback.print_exc()
                    break

        except Exception as e:
            print(f"\n Fatal Error: {e}")
            import traceback
            traceback.print_exc()
