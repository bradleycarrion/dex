import os
import copy
import datetime
import shutil

import click
import treelib

from dex.project import Project
from dex.executor import Executor
from dex.logic import rank_tasks
from dex.util import TerminalStyle, initiate_editor, AttrDict
from dex.constants import status_primitives, hold_str, done_str, abandoned_str, ip_str, todo_str, \
    executor_all_projects_key, valid_project_ids, importance_primitives, effort_primitives, max_due_date, due_date_fmt, today_in_executor_format, valid_recurrence_times, recurring_flag, no_flags

'''
# Top level commands
--------------------
dex init [root path]                                # create a new executor file and save the path somewhere
dex exec                                            # print and start work on the highest importance task, printing all info
dex info                                            # output some info about the current projects
dex example                                         # create an example directory and set the current project to it


# Executor commands
-------------------
dex executor                                        # view weekly schedule
dex executor edit                                   # edit the schedule file


# Project commands
-------------------
dex projects
dex project new                                     # make a new project
dex project [id]                                    # show all tasks for this project, ordered by priority             
dex project [id] exec                               # work on this specific project (not recommended)
dex project [id] rename                             # rename a project
dex project [id] rm                                 # delete a project


# Task commands
-------------------
dex tasks                                           # view ordered tasks across projects (default relevant to today, ordered by computed priority)
    Filtering (exclusive) options
    (--by-importance/-i)                            # Tasks ranked strictly by importance
    (--by-effort/-e)                                # Tasks ranked strictly by effort
    (--by-due/-d)                                   # Tasks ranked strictly by due date
    (--by-status/-s)                                # Tasks organized by status, ranked internally by computed priority
    (--by-project/-p)                               # Tasks organized by project, ranked internally by computed priority
    
    Additional options
    (--n-shown/-n [val])                            # limit to this number of total tasks shown
    (--all-projects/-a)                             # show across all projects, not just today
    (--include-inactive)                            # show inactive (done+abandoned) tasks
    
dex task                                            # make a new task
dex task [dexid]                                    # view a task
dex task [dexid] edit                               # edit a task
dex task [dexid] rename                             # rename a task
    
dex task [dexid] set ...                            # set an attribute of a task
    (--importance/-i [val]) 
    (--efort/-e [val]) 
    (--due/-d [val) 
    (--status/-s [status])
    (--recurring/-r [days])


# Task aliases
-------------------
dex task [dexid] imp [val]
dex task [dexid] eff [val]
dex task [dexid] due [val] 
    (--recurring/-r [days])

dex task [dexid] exec
dex task [dexid] done
dex task [dexid] todo
dex task [dexid] hold
dex task [dexid] aban <<alias for abandon>>
'''

# Constants
PROJECT_SUBCOMMAND_LIST = ["work", "view", "prio", "rename", "rm"]
TASK_SUBCOMMAND_LIST = PROJECT_SUBCOMMAND_LIST + ["edit", "hold", "done"]
CONTAINER_DIR = os.path.dirname(os.path.abspath(__file__))
CURRENT_ROOT_PATH_LOC = os.path.join(CONTAINER_DIR, "current_root.path")
CURRENT_ROOT_IGNORE_LOC = os.path.join(CONTAINER_DIR, "current_root.ignore")
REFERENCE_PROJSET_PATH = os.path.join(CONTAINER_DIR, "assets/reference_executor")
MAX_ENTRY_RETRIES = 3

STATUS_COLORMAP = {"todo": "b", "ip": "y", "hold": "m", "done": "g", "abandoned": "k"}
SUCCESS_COLOR = "c"
ERROR_COLOR = "r"
ts = TerminalStyle()


# Utility functions for getting the current root path
########################################################################################################################
def checks_root_path_loc():
    if os.path.exists(CURRENT_ROOT_PATH_LOC):
        # print("debug: current root path loc exists!")
        with open(CURRENT_ROOT_PATH_LOC, "r") as f:
            path = f.read()
            if os.path.exists(path):
                # print("debug: current root path exists!")
                return None
    print("No current projects. Use 'dex init' to start your set of projects or move to a new one.")
    click.Context.exit(1)


def get_current_root_path():
    with open(CURRENT_ROOT_PATH_LOC, "r") as f:
        p = f.read()
    return p


def write_path_as_current_root_path(path: str):
    with open(CURRENT_ROOT_PATH_LOC, "w") as f:
        f.write(path)


def write_ignore(ignore):
    with open(CURRENT_ROOT_IGNORE_LOC, "w") as f:
        for i in ignore:
            f.write(i + "\n")


def get_current_ignore():
    with open(CURRENT_ROOT_IGNORE_LOC, "r") as f:
        i = f.readlines()
    return [folder.replace("\n", "") for folder in i]


# Utility functions for common CLI tasks
########################################################################################################################
def get_project_header_str(project):
    id_str = ts.f("w", ts.f("u", f"Project {project.id}: {project.name}")) + " ["
    for sp in status_primitives:
        sp_str = "held" if sp == hold_str else sp
        id_str += ts.f(STATUS_COLORMAP[sp], f"{len(project.tasks[sp])} {sp_str}") + ", "
    id_str = id_str[:-2] + "]"
    return id_str


def get_task_string(t, colorize_status=False, id_color="x", name_color="x", attr_color="x", show_details=True):
    recurrence, recurring_n_days = t.recurrence

    if colorize_status:
        status_str = ts.f(STATUS_COLORMAP[t.status], t.status)
    else:
        status_str = t.status
    recurrence_str = f"recurs after {recurring_n_days} days" if recurrence else "non-recurring"
    id_str = ts.f(id_color, f"{t.dexid}")
    name_str = ts.f(name_color, f"{t.name}")
    date_str = t.due.strftime(due_date_fmt)

    dtd = t.days_till_due

    if dtd > 0:
        due_str = f"due in {t.days_till_due} days"
    elif dtd == 0:
        due_str = f"due today"
    else:
        due_str = f"overdue by {abs(t.days_till_due)} days"
    if show_details:
        attr_str = ts.f(attr_color, f"[{due_str} ({date_str}), {t.importance} importance, {t.effort} effort, {recurrence_str}]")
    else:
        attr_str = ""
    return f"{id_str} ({status_str}) - {name_str} {attr_str}"


def print_projects(pmap, show_n_tasks=3, show_inactive=False, **get_task_str_kwargs):
    tree = treelib.Tree()
    tree.create_node("All projects", "root")
    i = 0
    for p in pmap.values():
        id_str = get_project_header_str(p)
        tree.create_node(id_str, p.id, parent="root")
        if show_n_tasks:
            ordered_tasks = rank_tasks(p.tasks, limit=show_n_tasks, include_inactive=show_inactive)
            if ordered_tasks:
                for task in ordered_tasks:
                    task_txt = get_task_string(task, **get_task_str_kwargs)
                    tree.create_node(task_txt, i, parent=p.id)
                    i += 1
                if len(p.tasks.all) - len(p.tasks.done + p.tasks.abandoned) > show_n_tasks:
                    tree.create_node("...", i, parent=p.id)
                    i += 1
            else:
                tree.create_node("No tasks.", i, parent=p.id)
                i += 1
    tree.show(key=lambda node: node.identifier)


def ask_for_yn(prompt, action=None):
    for i in range(3):
        ans = input(f"{prompt} (y/n) ").lower()
        if ans in ("y", "yes"):
            if not isinstance(action, type(None)):
                action()
            return True
        elif ans in ("n", "no"):
            return False
        else:
            print("Please enter `y` or `n`")
    else:
        print(ts.f("r", "No input recieved. Get to work!"))
        click.Context.exit(1)


def print_task_work_interface(task):
    print(ts.f("u", f"Task {get_task_string(task)}"))
    ask_for_yn("View this task?", action=task.view)
    task.set_status(ip_str)
    print(ts.f(SUCCESS_COLOR, f"You're now working on '{task.name}'"))
    print(ts.f("y", "Now get to work!"))


def print_task_collection(project, show_done=False, n_shown=100):
    task_collection = project.tasks
    active_statuses = ["todo", "doing", "hold", "done"]
    if not show_done:
        active_statuses.remove("done")
    id_str = get_project_header_str(project)
    tree = treelib.Tree()
    tree.create_node(id_str, "header")
    nid = 0
    for sp in active_statuses:
        color = STATUS_COLORMAP[sp]
        tree.create_node(ts.f(color, sp.capitalize()), sp, parent="header")
        statused_tasks = task_collection[sp]
        if not statused_tasks:
            nid += 1
            tree.create_node("No tasks.", nid, parent=sp)
            continue
        for i, t in enumerate(statused_tasks):
            nid += 1
            task_txt = f"{t.id} - {t.name} (priority {t.priority})"
            tree.create_node(ts.f(color, task_txt), nid, parent=sp)
            if i >= n_shown:
                break
    tree.show(key=lambda node: node.identifier)


# Utility functions for checking tasks and projects
########################################################################################################################
def check_project_id_exists(pmap, project_id):
    if project_id not in pmap.keys():
        print(ts.f(ERROR_COLOR, f"Project ID {project_id} invalid. Select from the following projects:"))
        print_projects(pmap, show_n_tasks=0)
        click.Context.exit(1)


def check_task_id_exists(project, tid):
    if tid not in project.task_map.keys():
        print(ts.f(ERROR_COLOR, f"Task ID {tid} invalid. Select from the following tasks in project '{project.name}':"))
        print_task_collection(project)
        click.Context.exit(1)


def check_input_not_empty(input_str):
    if input_str is None or not input_str.strip():
        print(ts.f(ERROR_COLOR, "Empty or space-only names not allowed."))
        click.Context.exit(1)


# Global context level commands ########################################################################################
# dex
@click.group(invoke_without_command=False)
@click.pass_context
def cli(ctx):
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand != "init":
        checks_root_path_loc()
        e = Executor(path=get_current_root_path(), ignored_dirs=get_current_ignore())
        ctx.obj["EXECUTOR"] = e
        ctx.obj["PMAP"] = e.project_map


# Root level commands ##################################################################################################
# dex init
@cli.command(help="Initialize a new set of projects. You can only have one active.")
@click.argument('path', nargs=1, type=click.Path(file_okay=False, dir_okay=True, writable=True, readable=True))
@click.option("--ignore", "-i", multiple=True)
def init(path, ignore):
    if not ignore:
        ignore = tuple()
    descriptor = "existing" if os.path.exists(path) else "new"
    s = Executor(path=path, ignored_dirs=ignore)
    write_path_as_current_root_path(s.path)
    write_ignore(ignore)
    print(f"{descriptor.capitalize()} executor initialized in path: {path}")


# dex work
@cli.command(help="Automatically determine most important task and start work.")
@click.pass_context
def exec(ctx):
    e = ctx.obj["EXECUTOR"]
    tasks = e.get_n_highest_priority_tasks(1, include_inactive=False)
    if tasks:
        print_task_work_interface(tasks[0])
    else:
        print(ts.f(ERROR_COLOR, f"No tasks found for any project in executor {e.path}. Add a new task with 'dex task'"))



# @cli.command(help="Get info about your projects.")
# @click.option("--visualize", "-v", is_flag=True, help="Make a graph of current tasks.")
# @click.pass_context
# def info(ctx, visualize):
#     s = ctx.obj["SCHEDULE"]
#     print(f"The current dion working directory is {s.path}")
#     print(f"There are currently {len(s.get_projects())} projects.")
#     print(f"There are currently {len(s.get_n_highest_priority_tasks(n=10000, include_done=False))} active tasks.")
#     print(f"There are currently {len(s.get_n_highest_priority_tasks(n=10000, include_done=True))} total tasks, including done.")
#
#     if visualize:
#         projects = s.get_projects()
#         n_tasks_w_status = {sp: 0 for sp in status_primitives}
#         n_tasks_w_priority = {pp: 0 for pp in priority_primitives}
#         for p in projects:
#             tasks = p.tasks
#             for sp in status_primitives:
#                 n_tasks_w_status[sp] += len(tasks[sp])
#             for pp in priority_primitives:
#                 n_tasks_w_priority[pp] += len([t for t in tasks.all if t.priority == pp and t.status != done_str])
#
#         seaborn.set_style("darkgrid")
#         fig, axes = plt.subplots(nrows=1, ncols=2, figsize=(10, 5))
#         ax_status, ax_prio = axes
#
#         seaborn.barplot([f"priority {pp}" for pp in priority_primitives], [n_tasks_w_priority[pp] for pp in priority_primitives], ax=ax_prio, palette=seaborn.color_palette("Blues_r", len(priority_primitives)))
#         ax_prio.set_title("Current active (not done) tasks by priority")
#         seaborn.barplot(list(status_primitives), [n_tasks_w_status[sp] for sp in status_primitives], ax=ax_status, palette=seaborn.color_palette("Reds_r", len(status_primitives)))
#         ax_status.set_title("All tasks by status")
#
#         fig.tight_layout()
#         plt.show()


# dex example [root path]
# @cli.command(help="Get info about your projects. Enter a new folder path for the project directory!")
# @click.argument("path", type=click.Path(file_okay=False, dir_okay=False))
# def example(path):
#     if os.path.exists(path):
#         print(f"Path {path} exists. Choose a new path.")
#     shutil.copytree(REFERENCE_PROJSET_PATH, path)
#     print(f"New example created at {path}. Use 'dion init {path}' to initialize it and start work!")


# Schedule level commands ##############################################################################################
# dion schedule
@cli.group(invoke_without_command=True, help="Weekly executor (schedule) related commands.")
@click.pass_context
def executor(ctx):
    s = ctx.obj["EXECUTOR"]
    pmap = ctx.obj["PMAP"]
    tree = treelib.Tree()
    tree.create_node(ts.f("u", "Executor Schedule"), "root")
    i = 0
    for day, project_ids in s.executor_week.items():
        if project_ids == executor_all_projects_key:
            valid_pids = list(pmap.keys())
        else:
            valid_pids = project_ids

        is_today = day == datetime.datetime.today().strftime("%A")
        color = "c" if is_today else "w"
        tree.create_node(ts.f(color, day), day, data=i, parent="root")
        i += 1

        if not valid_pids:
            tree.create_node(ts.f("r", "No projects for this day"), data=i, parent=day)
        else:
            for j, pid in enumerate(valid_pids):
                project_txt = f"{pmap[pid].name}"
                color = "c" if is_today else "x"
                tree.create_node(ts.f(color, project_txt), data=j, parent=day)
    tree.show(key=lambda node: node.data)


# dex executor edit
@executor.command(name="edit", help="Edit your weekly schedule via project ids.")
@click.pass_context
def executor_edit(ctx):
    s = ctx.obj["EXECUTOR"]
    initiate_editor(s.executor_file)
    print(f"Weekly schedule at {s.executor_file} written.")


# Project level commands ###############################################################################################
# dex projects
@cli.command(help="List all projects.")
@click.pass_context
def projects(ctx):
    s = ctx.obj["EXECUTOR"]
    if s.projects:
        print_projects(s.project_map, show_n_tasks=0)
    else:
        print(ts.f(ERROR_COLOR, "No projects. Use 'dion project new' to create a new project."))


# dex project
# dex project new
@cli.group(invoke_without_command=True, help="Command a single project \n(do 'dex project new' w/ no args for new project).")
@click.argument("project_id", nargs=1, type=click.STRING, required=False)
@click.pass_context
def project(ctx, project_id):

    # Avoid scenario where someone types "dion project view" and it interprets "view" as the project id
    if project_id in PROJECT_SUBCOMMAND_LIST:
        print(ts.f(ERROR_COLOR, f"To access command '{project_id}' use 'dion project [PROJECT_ID] '{project_id}'."))
        click.Context.exit(1)
    else:
        if ctx.invoked_subcommand is None:
            # new project
            if project_id == "new":
                project_name = input("Enter new project name: ")
                check_input_not_empty(project_name)
                current_pids = ctx.obj["PMAP"].keys()
                remaining_pids = copy.deepcopy(valid_project_ids)
                for pid in current_pids:
                    remaining_pids.remove(pid)
                new_pid = remaining_pids[0]
                e = ctx.obj["EXECUTOR"]
                new_path = os.path.join(e.path, project_name)
                p = Project.new(path=new_path, id=new_pid)
                ignored_dirs = ctx.obj["EXECUTOR"].ignored_dirs
                e = Executor(path=get_current_root_path(), ignored_dirs=ignored_dirs)
                print(f"Project `{p.name}` added.")
                print_projects(e.project_map, show_n_tasks=0)
            else:
                pmap = ctx.obj["PMAP"]
                check_project_id_exists(pmap, project_id)
                ctx.obj["PROJECT"] = pmap[project_id]

                # view the task
                if project_id is None:
                    print_task_collection(pmap[project_id], show_done=True, n_shown=10000)


# dex project [project_id] exec
@project.command(name="exec", help="Automatically determine most important task in a project.")
@click.pass_context
def project_exec(ctx):
    p = ctx.obj["PROJECT"]
    tasks = rank_tasks(p.tasks)
    if tasks:
        print_task_work_interface(tasks[0])
    else:
        print(ts.f(ERROR_COLOR, f"No tasks found for Project {p.id}: '{p.name}'"))


# dex project [project_id] rename
@project.command(name="rename", help="Rename a project.")
@click.pass_context
def project_rename(ctx):
    p = ctx.obj["PROJECT"]
    old_name = copy.deepcopy(p.name)
    new_name = input("New project name: ")
    check_input_not_empty(new_name)
    p.rename(new_name)
    print(f"Project '{old_name}' renamed to '{p.name}.")


# dex project [project_id] rm
@project.command(name="rm", help="Remove a project and all of its tasks.")
@click.pass_context
def project_rm(ctx):
    p = ctx.obj["PROJECT"]
    name = copy.deepcopy(p.name)
    shutil.rmtree(p.path)
    print(f"Project '{name}' removed!")


# Task level commands ##################################################################################################
# dex tasks
@cli.command(help="List all (or just some) tasks. By default, organizes by computed priority, and only uses projects for today.")

### Task collection options
@click.option("--n-shown", "-n", help="Number of tasks shown (default is all tasks).", type=click.INT)
@click.option("--all-projects", "-a", is_flag=True, help="Show tasks across all the executor's projects, not just today's.")
@click.option("--include-inactive", "-v", is_flag=True, help="Show done and abandoned tasks.")
@click.option("--hide-task-details", "-h", is_flag=True, help="show task details")
### Ordering options
@click.option("--by-project", '-p', is_flag=True, help="Organize tasks by project. n_shown is shown for each project.")
@click.option("--by-importance", '-i', is_flag=True, help="Organize tasks by importance.")
@click.option("--by-effort", '-e', is_flag=True, help="Organize tasks by effort.")
@click.option("--by-due", '-d', is_flag=True, help="Organize tasks by due date.")
@click.option("--by-status", "-s", is_flag=True, help="Organize tasks by status.")
@click.pass_context
def tasks(ctx, n_shown, all_projects, include_inactive, hide_task_details, by_due, by_status, by_project, by_importance, by_effort):
    orderings = [by_due, by_status, by_project, by_importance, by_effort]
    if sum(orderings) > 1:
        print(ts.f("r", "Please only specify one ordering/organization option (--by-(project/importance/effort/due/status))"))

    show_task_details = not hide_task_details
    if n_shown is None:
        n_shown = 10000
        n_shown_str = "All"
    else:
        n_shown = int(n_shown)
        n_shown_str = f"Top {n_shown}"
    e = ctx.obj["EXECUTOR"]

    only_today = not all_projects
    only_today_str = f"today's projects only" if only_today else "all projects"

    pmap = e.project_map_today if only_today else e.project_map
    tasks_ordered = e.get_n_highest_priority_tasks(n_shown, only_today=only_today, include_inactive=include_inactive)

    tree = treelib.Tree()
    header_txt = f"{n_shown_str} tasks for {only_today_str}"
    if not any(orderings):
        header_txt += " (ordered by computed priority)"
        tree.create_node(ts.f("u", header_txt), "header")
        if tasks_ordered:
            for j, t in enumerate(tasks_ordered):
                if j < 3:
                    color = "c"
                elif 15 > j >= 3:
                    color = "y"
                else:
                    color = "k"

                task_txt = get_task_string(t, colorize_status=True, id_color=color, name_color=color, attr_color="x", show_details=show_task_details)
                # task_txt = ts.f(color, task_txt)
                tree.create_node(task_txt, j, parent="header")
            if len(tasks_ordered) > n_shown:
                tree.create_node("...", j + 1, parent="header")
        else:
            tree.create_node("No tasks", parent="header")
        tree.show(key=lambda node: node.identifier)
    elif by_project:
        print_projects(pmap, show_n_tasks=n_shown, show_inactive=include_inactive, colorize_status=True, show_details = show_task_details)
    elif by_due:

        legend_tree = treelib.Tree()
        legend_tree.create_node("Due date color legend", "header")
        legend_tree.create_node(ts.f("r", "Overdue or due today"), 1, parent="header")
        legend_tree.create_node(ts.f("y", "Due within one week"), 2, parent="header")
        legend_tree.create_node(ts.f("g", "Due within one month"), 3, parent="header")
        legend_tree.create_node(ts.f("b", "Due in 1+ months"), 4, parent="header")
        legend_tree.show(key=lambda node: node.identifier)

        ordered_by_due = sorted(tasks_ordered, key=lambda t: t.days_till_due)
        header_txt += " (ordered by due date)"
        tree.create_node(ts.f("u", header_txt), "header")

        for i, t in enumerate(ordered_by_due):
            dtd = t.days_till_due
            due_date_str = t.due.strftime(due_date_fmt)
            if dtd < 0:
                color = "r"
            elif dtd == 0:
                color = "r"
            elif dtd < 7:
                color = "y"
            elif dtd < 30:
                color = "g"
            else:
                color = "b"
            task_txt = get_task_string(t, colorize_status=False, name_color=color, show_details=show_task_details)
            tree.create_node(task_txt, i, parent="header")
        tree.show(key=lambda node: node.identifier)

    elif by_status:
        tree.create_node(ts.f("u", header_txt + " (ordered by status)"), "header")
        ordered_by_status = {sp: [] for sp in status_primitives}

        # this will already be ordered by computed priortiy
        for task in tasks_ordered:
            ordered_by_status[task.status].append(task)

        node_id = 0
        for sp in [todo_str, ip_str, hold_str, done_str, abandoned_str]:
            task_list = ordered_by_status[sp]
            subheader_id = f"subheader_{sp}"

            sp_str = "In progress" if sp == ip_str else sp.capitalize()
            tree.create_node(ts.f(STATUS_COLORMAP[sp], sp_str), subheader_id, parent="header")
            for i, task in enumerate(task_list):
                node_id += 1
                task_txt = get_task_string(task, colorize_status=False, show_details=show_task_details)
                tree.create_node(task_txt, node_id, parent=subheader_id)

        tree.show(key=lambda node: node.identifier)





# dex task
# dex task new
@cli.group(invoke_without_command=True, help="Commands for a single task (do 'dex task new' w/ no args for new task).")
@click.argument("task_id", nargs=1, type=click.STRING, required=False)
@click.pass_context
def task(ctx, task_id):
    pmap = ctx.obj["PMAP"]

    # Avoid scenario where someone types "dion task view" and it interprets "view" as the project id
    if task_id in TASK_SUBCOMMAND_LIST:
        print(ts.f(ERROR_COLOR, f"To access command '{task_id}' use 'dion task [DEX_ID] '{task_id}'."))
        click.Context.exit(1)
    else:
        if ctx.invoked_subcommand is None and task_id == "new":
            # select project
            header_txt = "Select a project id from the following projects:"
            print(header_txt + "\n" + "-" * len(header_txt))
            print_projects(pmap, show_n_tasks=0)
            project_id = input("Project ID: ")
            check_input_not_empty(project_id)
            check_project_id_exists(pmap, project_id)
            project = pmap[project_id]

            # enter task specifics
            task_name = input("Enter a name for this task: ")
            check_input_not_empty(task_name)

            task_due, task_imp, task_eff, task_status, task_flags = None, None, None, None, None

            for _ in range(MAX_ENTRY_RETRIES):
                task_imp = int(input(
                    f"Enter the task's importance ({importance_primitives[0]} - {importance_primitives[-1]}, higher is more important): "))
                if task_imp not in importance_primitives:
                    print(ts.f(ERROR_COLOR, f"'{task_imp}' is not a valid importance value. Choose from {importance_primitives}"))
                    continue
                else:
                    break
            else:
                print(ts.f(ERROR_COLOR, "Could not parse importance, exiting..."))
                click.Context.exit(1)

            for _ in range(MAX_ENTRY_RETRIES):
                task_eff = int(input(
                    f"Enter the how much effort the task will take ({effort_primitives[0]} - {effort_primitives[-1]}, higher is more effort): "))
                if task_eff not in effort_primitives:
                    print(ts.f(ERROR_COLOR, f"'{task_eff}' is not a valid effort value. Choose from {effort_primitives}"))
                    continue
                else:
                    break
            else:
                print(ts.f(ERROR_COLOR, "Could not parse effort, exiting..."))
                click.Context.exit(1)

            for _ in range(MAX_ENTRY_RETRIES):
                task_status = input(
                    f"Enter the task's status {status_primitives}, or hit enter to mark as {todo_str}: "
                )
                task_status = todo_str if not task_status else task_status
                if task_status not in status_primitives:
                    print(ts.f(ERROR_COLOR, f"'{task_status}' is not a valid status. Choose from {status_primitives}"))
                    continue
                elif task_status == done_str:
                    print(ts.f(ERROR_COLOR, "You can't make a new task as done. Stop wasting time."))
                    click.Context.exit(1)
                else:
                    break
            else:
                print(ts.f(ERROR_COLOR, "Could not parse status, exiting..."))
                click.Context.exit(1)

            for _ in range(MAX_ENTRY_RETRIES):
                task_due = input(
                    f"Enter the task's due date, (YYYY-MM-DD date or # days due from today) \n(press enter for the max due date, 365 days from now): "
                )
                if not task_due:
                    task_due = max_due_date
                    break
                else:
                    try:
                        task_due_int = int(task_due)
                        task_due = datetime.datetime.today() + datetime.timedelta(days=task_due_int)
                        break
                    except ValueError:
                        try:
                            task_due = datetime.datetime.strptime(task_due, due_date_fmt)
                            break
                        except ValueError:
                            print(ts.f(ERROR_COLOR, f"The entry '{task_due}' could not be parsed as a date or number of days."))
                            continue
            else:
                print(ts.f(ERROR_COLOR, "Could not parse due date, exiting..."))
                click.Context.exit(1)

            if ask_for_yn("Is the task recurring?"):
                for _ in range(MAX_ENTRY_RETRIES):
                    n_days_recurring = int(input(
                        "Enter the number of days after the due date that this task should recur: "
                    ))
                    if n_days_recurring not in valid_recurrence_times:
                        print(ts.f(ERROR_COLOR,
                                   f"'{n_days_recurring}' is not a valid recurrence interval. Choose a number of days between {valid_recurrence_times[0]} - {valid_recurrence_times[-1]}"))
                        continue
                    else:
                        task_flags = [f"r{n_days_recurring}"]
                        break
            else:
                task_flags = ["n"]

            edit_content = ask_for_yn("Edit the task's content?", action=None)
            # create new task
            t = project.create_new_task(task_name, task_eff, task_due, task_imp, task_status, task_flags, edit_content)
            footer_txt = f"Task created: {get_task_string(t)}"
            print("\n" + "-" * len(footer_txt) + "\n" + footer_txt)

        elif ctx.invoked_subcommand is None and task_id is None:
            click.echo(ctx.get_help())
            click.Context.exit(0)
        else:
            try:
                int(task_id[1:])
            except ValueError:
                print(ts.f(ERROR_COLOR, f"Task {task_id} not parsed. Task ids are a letter followed by a number. For example, 'a1'."))
                click.Context.exit(1)
            project_id = task_id[0]
            check_project_id_exists(pmap, project_id)
            p = pmap[project_id]
            check_task_id_exists(p, task_id)
            t= p.task_map[task_id]
            ctx.obj["TASK"] = t

            # dex task [dexid] (view it)
            if task_id is not None and ctx.invoked_subcommand is None:
                print(get_task_string(t, colorize_status=True), "\n")
                print(t.view())

# dex task [dexid] edit
@task.command(name="edit", help="Edit a task's content.")
@click.pass_context
def task_edit(ctx):
    t = ctx.obj["TASK"]
    t.edit()
    print(f"Task {t.dexid}: '{t.name}' edited.")


# dex task [dexid] rename
@task.command(name="rename", help="Rename a task.")
@click.pass_context
def task_rename(ctx):
    t = ctx.obj["TASK"]
    old_name = copy.deepcopy(t.name)
    print(f"Old name: {old_name}")
    new_name = input("New name: ")
    check_input_not_empty(new_name)
    t.rename(new_name)
    print(f"Task {t.dexid} renamed from '{old_name}' to '{t.name}'.")


# dex task [dexid] set [args]
@task.command(name="set", help="Change a task's importance, effort, status, and/or due date and recurrence.")
@click.option("--importance", "-i", help=f"Set a task's importance {importance_primitives}", type=click.INT)
@click.option("--effort", "-e", help=f"Set a task's effort {effort_primitives}", type=click.INT)
@click.option("--status", "-s", help=f"Set a task's status {status_primitives}", type=click.STRING)
@click.option("--due", "-d", help=f"Set a task's due date (YYYY-MM-DD or # days until due")
@click.option("--recurring", "-r", help="Change or enable task recurrence (1-365 day intervals). Enter the number of days until it recurs (0 to make the task not recurring", type=click.INT)
@click.pass_context
def task_set(ctx, importance, effort, status, due, recurring):
    has_error = False
    if importance is not None and int(importance) not in importance_primitives:
        print(ts.f(ERROR_COLOR, f"{importance} not a valid importance value {importance_primitives}"))
        has_error = True
    if effort is not None and int(effort) not in effort_primitives:
        print(ts.f(ERROR_COLOR, f"{effort} not a valid effort value {effort_primitives}"))
        has_error = True
    if status is not None and status not in status_primitives:
        print(ts.f(ERROR_COLOR, f"{status} not a valid status {status_primitives}"))
        has_error = True

    task_due = None
    if due is not None:
        try:
            task_due_int = int(due)
            task_due = datetime.datetime.today() + datetime.timedelta(days=task_due_int)
        except ValueError:
            try:
                task_due = datetime.datetime.strptime(due, due_date_fmt)
            except ValueError:
                print(ts.f(ERROR_COLOR, f"The entry '{due}' could not be parsed as a date or number of days."))
                has_error = True

    if recurring is not None:
        recurring = int(recurring)
        if recurring not in valid_recurrence_times and recurring != 0:
            print(ts.f(ERROR_COLOR, f"{recurring} not a valid recurrence time."))
            has_error = True

    print(importance, effort, status, due, recurring)

    if has_error:
        print(ts.f(ERROR_COLOR, f"Errors encountered during argument parsing. Task not updated. See `dex task [dexid] set for more information."))
        click.Context.exit(1)
    else:
        t = ctx.obj["TASK"]
        if importance is not None:
            t.set_importance(importance)
        if effort is not None:
            t.set_effort(effort)
        if due is not None:
            t.set_due(task_due)
        if status is not None:
            print(f"Changing status to {status}")
            t.set_status(status)
        if recurring is not None:
            recurring_flags = [f for f in t.flags if recurring_flag in f]
            for f in recurring_flags:
                t.rm_flag(f)

            # if r is 0, all the recurrences have been removed, so only do stuff if r != 0
            if recurring == 0:
                t.add_flag(no_flags)
            if recurring != 0:
                t.add_flag(f"r{recurring}")
        success_text = ts.f(SUCCESS_COLOR, f"Task {t.dexid} successfully updated to:")
        print(f"{success_text}\n{get_task_string(t)}\n")


if __name__ == '__main__':
    cli(obj={})