"""Historical direct compatibility reexports for the Platform lock owner."""

from llm_agent.filesystem.lock_filesystem import (
    UnsafeLockPathError as UnsafeLockPathError,
)
from llm_agent.filesystem.lock_filesystem import (
    descriptor_matches_path as descriptor_matches_path,
)
from llm_agent.filesystem.lock_filesystem import (
    is_safe_regular as is_safe_regular,
)
from llm_agent.filesystem.lock_filesystem import (
    open_verified as open_verified,
)
from llm_agent.filesystem.lock_filesystem import (
    sync_parent_directory as sync_parent_directory,
)
from llm_agent.filesystem.lock_filesystem import (
    unlink_if_observed as unlink_if_observed,
)
from llm_agent.filesystem.lock_filesystem import (
    unlink_if_same_stat as unlink_if_same_stat,
)
from llm_agent.filesystem.lock_filesystem import (
    validate_final_entry as validate_final_entry,
)

__all__ = ['UnsafeLockPathError', 'descriptor_matches_path', 'is_safe_regular', 'open_verified', 'sync_parent_directory', 'unlink_if_observed', 'unlink_if_same_stat', 'validate_final_entry']
