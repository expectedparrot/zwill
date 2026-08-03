"""Report rendering facade.

Implementation lives in three focused modules:

* ``report_common`` — shared helpers, the report CSS, and markdown utilities.
* ``probability_report`` — one-shot probability report rendering.
* ``twin_report_html`` — digital-twin report renderers.

This module re-exports them so existing ``from .reporting import ...`` call
sites keep working unchanged.
"""

from __future__ import annotations

from .probability_report import *  # noqa: F401,F403
from .report_common import *  # noqa: F401,F403
from .twin_report_html import *  # noqa: F401,F403
