"""Directory names shared with OptimalX Desktop."""

from eidos.integrations.notex_paths import assign_directory_names, sanitize_segment


def _row(**fields):
    return {
        "parent_name": fields["parent_name"],
        "parent_created_at": fields["parent_created_at"],
        "parent_global_id": fields["parent_global_id"],
        "subfolder_name": fields["subfolder_name"],
        "subfolder_created_at": fields["subfolder_created_at"],
        "subfolder_global_id": fields["subfolder_global_id"],
    }


def test_sanitize_slash_and_empty():
    assert sanitize_segment("Code Critic / JS Build", "gid") == "Code Critic - JS Build"
    assert sanitize_segment("Code\\Critic", "gid") == "Code - Critic"
    assert sanitize_segment("   ", "fallback-id") == "fallback-id"
    assert sanitize_segment("...", "fallback-id") == "fallback-id"


def test_duplicate_parent_and_subfolder_get_numbers_and_chat_is_skipped():
    assigned = assign_directory_names([
        _row(
            parent_name="Job Search",
            parent_created_at=100,
            parent_global_id="parent-old",
            subfolder_name="Resume",
            subfolder_created_at=100,
            subfolder_global_id="resume-old",
        ),
        _row(
            parent_name="Job Search",
            parent_created_at=200,
            parent_global_id="parent-new",
            subfolder_name="__chat_9__",
            subfolder_created_at=150,
            subfolder_global_id="chat",
        ),
        _row(
            parent_name="Job Search",
            parent_created_at=200,
            parent_global_id="parent-new",
            subfolder_name="Resume",
            subfolder_created_at=200,
            subfolder_global_id="resume-new",
        ),
        _row(
            parent_name="Chess AI Book Project",
            parent_created_at=50,
            parent_global_id="chess",
            subfolder_name="chp 2",
            subfolder_created_at=100,
            subfolder_global_id="chp-old",
        ),
        _row(
            parent_name="Chess AI Book Project",
            parent_created_at=50,
            parent_global_id="chess",
            subfolder_name="chp 2",
            subfolder_created_at=200,
            subfolder_global_id="chp-new",
        ),
    ])
    assert "chat" not in assigned
    assert assigned["resume-old"]["parent_segment"] == "Job Search"
    assert assigned["resume-new"]["parent_segment"] == "Job Search 2"
    assert assigned["resume-new"]["subfolder_segment"] == "Resume"
    assert assigned["chp-old"]["subfolder_segment"] == "chp 2"
    assert assigned["chp-new"]["subfolder_segment"] == "chp 2 2"
