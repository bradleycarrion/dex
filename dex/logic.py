import random
from typing import List

from dion.util import AttrDict
from dion.task import Task
from dion.constants import priority_primitives


def rank_tasks(task_collection: AttrDict, limit: int = 0, include_inactive: bool = False) -> List[Task]:
    """
        Order a task collection

    1. remove abandoned and done tasks
    2. deprioritize held tasks
    3. rank todo and ip tasks by computed priority

    Args:
        task_collection (AttrDict): A collection of Tasks in dict/attr format with keys of status primitives.
        limit (int): Max number of tasks to return 
        include_inactive (bool): If True, includes the inactive (abandoned+done) tasks in the returned list

    Returns:
        [Task]: A list of ranked tasks.
    """

    # most important is low index

    if include_inactive:
        done_ordered = sorted(task_collection.done, key=lambda x: x.priority, reverse=True)
        random.shuffle(task_collection.abandoned)
        ordered = done_ordered + task_collection.abandoned
    else:
        ordered = []

    hold_ordered = sorted(task_collection.hold, key=lambda x: x.priority, reverse=True)
    ordered = hold_ordered + ordered

    todoing = sorted(task_collection.todo + task_collection.doing, key=lambda x: x.priority, reverse=True)

    # more advanced ordering for to-do + doing
    todoing_by_priority = {priority: [] for priority in priority_primitives}
    for t in todoing:
        todoing_by_priority[t.priority].append(t)

    if limit:
        return ordered[:limit]
    else:
        return ordered