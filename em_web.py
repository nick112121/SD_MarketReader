"""
Service entry point.

This used to be the single-NQ expected-move page. It now serves the
all-in-one 4-market page (ES / NQ / YM / RTY) implemented in four_em.py.

It's kept as a thin re-export so the DEPLOYED service — which launches
`uvicorn em_web:app` — picks up the new page on an ordinary code deploy,
with no change to the service's start command (so no Render Blueprint
"sync" is needed). `em_web:app` and `four_em:app` are now the same app.
"""

from four_em import app  # noqa: F401  (re-exported for `uvicorn em_web:app`)
