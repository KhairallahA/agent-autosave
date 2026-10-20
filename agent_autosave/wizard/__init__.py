"""The `agent-autosave init` setup wizard.

    flow     the entry point: the questions, in order
    screen   banner and layout (the left rail of steps), on top of ..ui
    widgets  text, select, multi-select and confirm, with arrow keys and space
    render   answers -> the files that will be created or changed
    write    atomic writes, with a backup of any file replaced

Only `init` imports this package; hooks never do.
"""
