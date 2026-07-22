from agent.planning.execution_gateway import ExecutionGateway


class _Skill:
    name = "echo"

    def get_schema(self):
        return {}


class _PlanExecutor:
    def __init__(self):
        self.calls = 0

    def execute(self, objective, tool_usage_count):
        self.calls += 1
        return None


class _State:
    def __init__(self):
        self.plan = []
        self.tool_history = []

    def set_plan(self, plan):
        self.plan = plan


class _Orchestrator:
    def __init__(self):
        self.skills = {"echo": _Skill()}
        self.active_skills = ["echo"]
        self.agent_state = _State()
        self.plan_executor = _PlanExecutor()
        self.verbose = False
        self.failed = False
        self.events = []

    def fail_task(self):
        self.failed = True

    def _emit(self, event_type, data=None):
        self.events.append((event_type, data or {}))


def test_gateway_validates_before_execution():
    orchestrator = _Orchestrator()
    gateway = ExecutionGateway(orchestrator)

    result = gateway.execute_validated_plan(
        [{"tool": "echo", "args": {}}],
        "objetivo",
        {},
    )

    assert result.aborted is False
    assert orchestrator.plan_executor.calls == 1
    assert orchestrator.agent_state.plan == result.validated_plan


def test_gateway_does_not_execute_invalid_plan():
    orchestrator = _Orchestrator()
    gateway = ExecutionGateway(orchestrator)

    result = gateway.execute_validated_plan([], "objetivo", {})

    assert result.aborted is True
    assert orchestrator.failed is True
    assert orchestrator.plan_executor.calls == 0
