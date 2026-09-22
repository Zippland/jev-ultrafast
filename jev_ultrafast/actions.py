"""Observed tools and finite parameters. No rules here interpret the user's speech."""

OPERATIONS = {
    "click": "CLICK", "fill": "TYPE_TEXT", "insert": "INSERT_TEXT", "select": "SELECT",
    "secondary": "SECONDARY_ACTION", "select_text": "SELECT_TEXT", "key": "PRESS_KEY",
    "scroll": "SCROLL", "navigate": "NAVIGATE", "new_tab": "NEW_TAB", "close_tab": "CLOSE_TAB",
    "activate_tab": "SHOW_TAB", "back": "BACK", "forward": "FORWARD", "reload": "RELOAD",
    "upload": "UPLOAD_FILE", "wait": "WAIT", "inspect": "SWITCH_APP", "screenshot": "SCREENSHOT",
    "activate_app": "SHOW_APP",
}
DESCRIPTIONS = {
    "CLICK": "Click an observed control; choose left, right, middle or double click.",
    "TYPE_TEXT": "Replace an editable field's content. The text model supplies the value, then the executor fills it.",
    "INSERT_TEXT": "Enter a contiguous sequence of literal characters through the current app keyboard input, "
                   "including numbers, operators, or prose accepted by that app. Replaces the current selection "
                   "when text is selected; otherwise inserts at the caret. Unselected content is preserved; "
                   "this does not clear fields, run code, or press shortcuts. A text model supplies the characters.",
    "SELECT": "Choose an observed dropdown option.",
    "SECONDARY_ACTION": "Perform an accessibility action explicitly offered by the observed element.",
    "SELECT_TEXT": "Select exact text inside an observed text element, or place the caret before or after it.",
    "PRESS_KEY": "Press a key or shortcut in the observed app or tab.",
    "SCROLL": "Scroll an observed page or container.",
    "NAVIGATE": "Open a URL in the observed browser tab. The text model supplies the URL.",
    "NEW_TAB": "Create a background browser tab with a URL supplied by the text model. "
               "It is not shown or foregrounded; SHOW_TAB does that separately.",
    "CLOSE_TAB": "Close this observed browser tab.",
    "SHOW_TAB": "Make this tab visible in Chrome, without changing its content.",
    "SHOW_APP": "Activate an observed running application in the macOS foreground. "
                "This does not open a new window, type text, or navigate a browser.",
    "BACK": "Go to the preceding observed browser history entry.",
    "FORWARD": "Go to the following observed browser history entry.",
    "RELOAD": "Reload the current browser tab.",
    "UPLOAD_FILE": "Set a local file on an observed file input; the text model supplies the explicit absolute path.",
    "WAIT": "Wait briefly for the interface to update, then observe again.",
    "SWITCH_APP": "Inspect an app or browser tab to obtain its controls, including the already foreground app. "
                  "Use this when its contents have not been observed yet and field tools are not available. "
                  "This does not launch, foreground, or navigate it; it is observation only.",
    "SCREENSHOT": "Save a screenshot of the observed browser tab to this session's trace folder.",
}
TEXT_ARGUMENTS = {
    "fill": "The exact replacement text for this field.",
    "insert": "The exact literal keyboard text to type at the current focus. This replaces the current selection "
              "if present, otherwise inserts at the caret; preserve unselected content. "
              "Do not encode modifier shortcuts or tool calls as text.",
    "select_text": "An exact, unambiguous substring of this element's observed value to select or locate.",
    "navigate": "The destination HTTP(S) URL. Return a URL, never JavaScript or code.",
    "new_tab": "The new tab's HTTP(S) URL, or about:blank for an empty tab.",
    "upload": "The explicitly supplied absolute path to one existing local file. Never invent a path.",
}
KEYS = {
    "ENTER": ("Return", "Enter", 0), "TAB": ("Tab", "Tab", 0),
    "SHIFT_TAB": ("shift+Tab", "Tab", 8), "ESCAPE": ("Escape", "Escape", 0),
    "BACKSPACE": ("BackSpace", "Backspace", 0), "DELETE": ("Delete", "Delete", 0),
    "LEFT": ("Left", "ArrowLeft", 0), "RIGHT": ("Right", "ArrowRight", 0),
    "UP": ("Up", "ArrowUp", 0), "DOWN": ("Down", "ArrowDown", 0),
    "HOME": ("Home", "Home", 0), "END": ("End", "End", 0),
    "PAGE_UP": ("Prior", "PageUp", 0), "PAGE_DOWN": ("Next", "PageDown", 0),
    "SPACE": ("space", " ", 0),
    **{label: (f"super+{key}", key, 4) for label, key in {
        "SELECT_ALL": "a", "COPY": "c", "PASTE": "v", "CUT": "x", "UNDO": "z",
        "SAVE": "s", "FIND": "f", "NEW_DOCUMENT": "n", "BOLD": "b", "ITALIC": "i",
    }.items()},
    "REDO": ("super+shift+z", "z", 12),
}
PARAMETERS = {
    "click": {"style": {"left": "Single left click", "double": "Double left click",
                         "right": "Right click / context menu", "middle": "Middle click"}},
    "key": {"key": {key: key.replace("_", " ").lower() for key in KEYS}},
    "select_text": {"selection": {"text": "Select the text", "cursor_before": "Caret before text",
                                  "cursor_after": "Caret after text"}},
    "scroll": {"direction": {d: d for d in ("up", "down", "left", "right")},
               "amount": {"quarter": "A little, one quarter page", "page": "One page", "three": "Three pages"}},
}
PAGES = {"quarter": 0.25, "page": 1, "three": 3}


def parameters_for(action):
    if action.get("control_surface_exit"):
        return {"key": {"NEW_DOCUMENT": "Open a new browser window, preserving the recording page"}}
    if action["kind"] == "scroll" and "delta" in action:
        return {}  # Original Ultrafast scroll choices already contain a direction and distance.
    return PARAMETERS.get(action["kind"], {})


def observed_action(action, actions):
    """Resolve model-selected finite parameters against the exact observed action."""
    base = {k: v for k, v in action.items() if k != "parameters"}
    if base not in actions:
        raise ValueError("Action is not part of the current observation")
    choices = parameters_for(base)
    params = action.get("parameters", {})
    if params and (set(params) != set(choices) or any(v not in choices[k] for k, v in params.items())):
        raise ValueError("Tool parameters are not offered choices")
    return base


def parameter_values(action):
    """Defaults preserve calls from the original fixed-goal agent and offline benchmarks."""
    defaults = {key: next(iter(values)) for key, values in parameters_for(action).items()}
    return {**defaults, **action.get("parameters", {})}
