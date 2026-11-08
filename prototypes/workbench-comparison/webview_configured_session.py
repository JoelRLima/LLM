"""WebView2 composition over the same configured startup request as CLI chat."""

from pathlib import Path


def create_configured_session(*, workspace=None, home=None, config_path=None, profile=None):
    # Lazy imports: constructing the window/offline fixture must not start Agent.
    from llm_agent.application.context import AppPaths
    from llm_agent.application.task_execution import TaskExecutionStart
    from llm_agent.interactive.session import InteractiveSession

    workspace_path = Path(workspace if workspace is not None else Path.cwd()).expanduser().resolve()
    start = TaskExecutionStart(
        workspace=workspace_path,
        app_paths=AppPaths.discover(app_home=home),
        config_path=config_path,
        profile=profile,
        configure_logging=False,
    )
    # The shared controller owns execution; Tk's WorkbenchSession is unchanged.
    # No gateway injection or process-wide replacement of startup functions.
    return InteractiveSession(start), workspace_path, None
