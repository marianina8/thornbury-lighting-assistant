# SPDX-License-Identifier: GPL-3.0-or-later
"""Thornbury Lighting Assistant: turn a plain-language lighting note into a
proposed set of spot or area light settings (including a physical snoot) that the artist reviews and applies.

The model only proposes numbers; this addon shows them as a diff and writes
nothing until the artist clicks Apply (one undo step)."""

from . import gallery, gobocard, jobs, ops, props, ui

_modules = (props, gallery, gobocard, ops, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    jobs.unregister()
    for m in reversed(_modules):
        m.unregister()
