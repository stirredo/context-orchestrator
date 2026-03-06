import tempfile
from pathlib import Path
import pytest
from context_orchestrator.db import Database


@pytest.fixture
def db():
    return Database(db_path=Path(tempfile.mktemp(suffix=".db")))


class TestTasks:
    def test_create_task(self, db):
        task = db.create_task("test-task", "A test task", "https://github.com/org/repo")
        assert task["name"] == "test-task"
        assert task["description"] == "A test task"
        assert task["project"] == "https://github.com/org/repo"

    def test_create_duplicate_task_raises(self, db):
        db.create_task("test-task", project="proj1")
        with pytest.raises(ValueError, match="already exists"):
            db.create_task("test-task", project="proj1")

    def test_same_name_different_project_ok(self, db):
        t1 = db.create_task("test-task", project="proj1")
        t2 = db.create_task("test-task", project="proj2")
        assert t1["id"] != t2["id"]

    def test_list_tasks(self, db):
        db.create_task("task-1", "First")
        db.create_task("task-2", "Second")
        tasks = db.list_tasks()
        assert len(tasks) == 2

    def test_list_tasks_by_project(self, db):
        db.create_task("task-1", project="proj1")
        db.create_task("task-2", project="proj2")
        tasks = db.list_tasks(project="proj1")
        assert len(tasks) == 1
        assert tasks[0]["name"] == "task-1"

    def test_list_tasks_includes_source_count(self, db):
        task = db.create_task("task-1")
        db.add_source(task["id"], "file", "/tmp/a.md")
        db.add_source(task["id"], "file", "/tmp/b.md")
        tasks = db.list_tasks()
        assert tasks[0]["source_count"] == 2

    def test_get_task_by_name(self, db):
        db.create_task("test-task", "desc")
        task = db.get_task_by_name("test-task")
        assert task["description"] == "desc"

    def test_get_task_by_name_with_project(self, db):
        db.create_task("test-task", project="proj1")
        db.create_task("test-task", project="proj2")
        task = db.get_task_by_name("test-task", project="proj2")
        assert task["project"] == "proj2"

    def test_get_nonexistent_task(self, db):
        assert db.get_task_by_name("nope") is None


class TestSources:
    def test_add_source(self, db):
        task = db.create_task("task-1")
        source = db.add_source(task["id"], "file", "/tmp/test.md", "My notes")
        assert source["source_type"] == "file"
        assert source["reference"] == "/tmp/test.md"
        assert source["notes"] == "My notes"

    def test_add_duplicate_source_raises(self, db):
        task = db.create_task("task-1")
        db.add_source(task["id"], "file", "/tmp/test.md")
        with pytest.raises(ValueError, match="already exists"):
            db.add_source(task["id"], "file", "/tmp/test.md")

    def test_same_source_different_tasks_ok(self, db):
        t1 = db.create_task("task-1")
        t2 = db.create_task("task-2")
        s1 = db.add_source(t1["id"], "file", "/tmp/test.md")
        s2 = db.add_source(t2["id"], "file", "/tmp/test.md")
        assert s1["id"] != s2["id"]

    def test_get_sources_for_task(self, db):
        task = db.create_task("task-1")
        db.add_source(task["id"], "file", "/tmp/a.md")
        db.add_source(task["id"], "repo", "https://github.com/org/repo")
        sources = db.get_sources_for_task(task["id"])
        assert len(sources) == 2

    def test_remove_source(self, db):
        task = db.create_task("task-1")
        source = db.add_source(task["id"], "file", "/tmp/test.md")
        assert db.remove_source(source["id"]) is True
        assert len(db.get_sources_for_task(task["id"])) == 0

    def test_remove_nonexistent_source(self, db):
        assert db.remove_source(9999) is False

    def test_cascade_delete(self, db):
        task = db.create_task("task-1")
        db.add_source(task["id"], "file", "/tmp/test.md")
        db.conn.execute("DELETE FROM tasks WHERE id = ?", (task["id"],))
        db.conn.commit()
        assert len(db.get_sources_for_task(task["id"])) == 0


class TestRepoKnowledge:
    def test_update_repo_knowledge(self, db):
        entry = db.update_repo_knowledge("https://github.com/org/repo", "Uses pytest")
        assert entry["insight"] == "Uses pytest"
        assert entry["repo_url"] == "https://github.com/org/repo"

    def test_multiple_insights_accumulate(self, db):
        db.update_repo_knowledge("https://github.com/org/repo", "Uses pytest")
        db.update_repo_knowledge("https://github.com/org/repo", "Needs Node 18")
        knowledge = db.get_repo_knowledge("https://github.com/org/repo")
        assert len(knowledge) == 2

    def test_get_repo_knowledge_empty(self, db):
        knowledge = db.get_repo_knowledge("https://github.com/org/nope")
        assert knowledge == []

    def test_knowledge_per_repo(self, db):
        db.update_repo_knowledge("https://github.com/org/repo1", "Insight A")
        db.update_repo_knowledge("https://github.com/org/repo2", "Insight B")
        k1 = db.get_repo_knowledge("https://github.com/org/repo1")
        k2 = db.get_repo_knowledge("https://github.com/org/repo2")
        assert len(k1) == 1
        assert len(k2) == 1
        assert k1[0]["insight"] == "Insight A"
