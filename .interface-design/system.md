# Interface patterns

Patterns adopted for more than one screen. Tokens and components live in `ui/src/index.css`.

- **Stage strip** (`ui/src/ArticleReader.tsx`, `.stage-strip`): the pipeline's stages as `aria-current` links above
  per-stage views, each showing its outcome as text (score, count, Not run, Failed). State is never shown by colour
  alone. The view lives in the URL (`?view=`).
- **Review by article** (`ui/src/ReviewPage.tsx`, `.queue-row`): a compact row per article (score badge, title and
  source, relevance, rule counts with the selected status bold). Rows open the article on its Rules view; reviewing
  happens there with `ReviewItem`, the same component the by-rule list uses.
