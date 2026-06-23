import re

with open("agent/orchestrator.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Imports
import_block = "from agent.parsers import extract_json, stringify, validate_decision, normalize_tool_result, extract_json_from_end"
new_import_block = import_block + "\nfrom agent.parsers import validate_tool_args\nfrom agent.workspace import WorkspaceManager\nfrom agent.context_manager import ContextManager"
code = code.replace(import_block, new_import_block)

# 2. __init__ replacements
code = code.replace("        self._cached_project_context: Optional[str] = None   # cache para contexto do projeto\n", "")
code = code.replace("        self._restore_points: List[Dict[str, str]] = []      # backup -> original\n", "")
init_injection = """        self.agent_state = AgentState()
        self.workspace = WorkspaceManager(verbose=self.verbose)
        self.context_manager = ContextManager(self.session, self.agent_state, verbose=self.verbose)
"""
code = code.replace("        self.agent_state = AgentState()\n", init_injection)

# 3. Remove _backup_memory_file call in save_memory_to_file
code = code.replace("        self._backup_memory_file(path)\n", "")

# 4. Method Replacements
replacements = {
    "self._validate_args(": "validate_tool_args(",
    "self._check_prompt_size()": "self.context_manager.check_prompt_size()",
    "self._count_tokens_precise(": "self.context_manager.count_tokens_precise(",
    "self._estimate_conversation_tokens()": "self.context_manager.estimate_conversation_tokens()",
    "self._build_compact_view()": "self.context_manager.build_compact_view()",
    "self._maybe_compress_context()": "self.context_manager.maybe_compress_context()",
    "self._get_file_hints(": "self.context_manager.get_file_hints(",
    "self._create_restore_point()": "self.workspace.create_restore_point(self.agent_state.plan)",
    "self._rollback()": "self.workspace.rollback()",
    "self._show_diff(": "self.workspace.show_diff(",
    "self._lint_check(": "self.workspace.lint_check("
}
for old, new in replacements.items():
    code = code.replace(old, new)

# special fixes for validate_args since its signature changed
code = code.replace("validate_tool_args(tool, args)", "validate_tool_args(tool, args, self.skills)")
code = code.replace("self._build_base_system_prompt()", 'self.context_manager.build_base_system_prompt(getattr(self, "current_persona_prompt", ""), self._build_tools_description(compact=False))')

# 5. Remove method definitions
methods_to_remove = [
    "_validate_args", "_get_project_context", "_estimate_conversation_tokens",
    "_maybe_compress_context", "_build_compact_view", "_get_file_hints",
    "_check_prompt_size", "_count_tokens_precise", "_build_base_system_prompt",
    "_backup_memory_file", "_create_restore_point", "_rollback",
    "_show_diff", "_lint_check"
]

for method in methods_to_remove:
    # Matche def method(...) -> ...: and any following lines that start with 8 spaces or are empty
    pattern = rf"    def {method}\(.*?\).*?:\n(?:        .*?\n|    \n|\n)*"
    code = re.sub(pattern, "", code, flags=re.MULTILINE | re.DOTALL)

with open("agent/orchestrator.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Refactored orchestrator.py")
