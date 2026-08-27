import ast
import inspect
import textwrap

from agent.planning.execution_gateway import ExecutionGateway
from agent.planning.plan_builder import PlanBuilder
from agent.planning.plan_optimizer import PlanOptimizer
from agent.planning.plan_validator import PlanValidator
from agent.planning.reactive_loop import ReactiveLoop


def _function_tree(function) -> ast.FunctionDef | ast.AsyncFunctionDef:
    source = textwrap.dedent(inspect.getsource(function)).lstrip()
    tree = ast.parse(source)
    return next(node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


def _names(tree: ast.AST) -> set[str]:
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def test_context_validator_does_not_read_legacy_skills() -> None:
    tree = _function_tree(PlanValidator._validate_context_step)
    assert "skills" not in _names(tree)


def test_context_optimizer_does_not_use_static_catalog() -> None:
    tree = _function_tree(PlanOptimizer._meta)
    assert "TOOL_METADATA" not in _names(tree)


def test_gateway_derives_view_from_effective_context() -> None:
    tree = _function_tree(ExecutionGateway._planning_view)
    assert any(
        isinstance(node, ast.Attribute) and node.attr == "resolve_view"
        for node in ast.walk(tree)
    )


def test_planner_prompts_have_no_broad_type_error_fallback() -> None:
    for function in (PlanBuilder._build_prompt, ReactiveLoop._build_prompt):
        tree = _function_tree(function)
        assert not any(
            isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "TypeError"
            for handler in ast.walk(tree)
        )
