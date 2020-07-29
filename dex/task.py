import os
import copy
import datetime
from typing import Union

import mdv

from dex.constants import dexcode_delimiter_left as ddl, dexcode_delimiter_mid as ddm, dexcode_delimiter_right as ddr, \
    status_primitives_ints as spi, status_primitives_ints_inverted as spi_inverted, effort_primitives, \
    importance_primitives, valid_project_ids, due_date_fmt, flags_primitives, dexcode_delimiter_flag, dexcode_header, \
    hold_str, done_str, ip_str, abandoned_str, todo_str, task_extension, inactive_subdir
from dex.util import initiate_editor


class Task:
    def __init__(self, dexid: str, path: str, effort: int, due: datetime.datetime, importance: int, status: str,
                 flags: list, edit_content: bool = False):
        """
        To not be confusingly stateful or slow, a Task object reflects a task (file) at a certain point in time.

        After the object is updated, you must self.write_state for the state of the object to be written to file.

        This class should only be used for changing Tasks which will remain in the same place (excluding renames).
        Moving files is handled by Project.
        """

        self.dexid = dexid
        self.path = path
        self.due = due
        self.effort = effort
        self.importance = importance
        self.status = status
        self.flags = list(set(flags))

        if not self.path.endswith(".md"):
            raise TypeError("Task files must be markdown, and must end in '.md'.")
        elif os.path.isdir(self.path):
            raise TypeError("Task cannot be a directory!")

        self.prefix_path = os.path.dirname(self.path)
        self.relative_filename = os.path.basename(self.path)
        self.name = os.path.splitext(self.relative_filename)[0]

        if edit_content:
            initiate_editor(self.path)

        content = ""
        if os.path.exists(self.path):
            with open(self.path, "r") as f:
                content = f.read()
        try:
            extract_dexcode_from_content(content)
            content = "\n".join(content.split("\n")[:-1])
        except ValueError:
            # No dexcode found, so just return all content including dexcode...
            pass

        self.content = content if content else ""

    def __str__(self):
        return f"<dex Task {self.dexid} | '{self.name}' " \
               f"(status={self.status}, due={self.due.strftime(due_date_fmt)}, " \
               f"effort={self.effort}, importance={self.importance}, flags={self.flags})"

    def __repr__(self):
        return self.__str__()

    @classmethod
    def from_file(cls, path: str):
        with open(path, "r") as f:
            content = f.read()
        dexcode = extract_dexcode_from_content(content)
        dexid, effort, due, importance, status, flags = decode_dexcode(dexcode)
        return cls(dexid, path, effort, due, importance, status, flags)

    @classmethod
    def from_spec(cls, *args, **kwargs):
        t = cls(*args, **kwargs)
        t._write_state()
        return t

    def _write_state(self):
        with open(self.path, "w") as f:
            f.write(self.content)
            dexcode = encode_dexcode(self.dexid, self.effort, self.due, self.importance, self.status, self.flags)
            f.write(f"\n{dexcode_header} {dexcode}")

    def edit(self) -> None:
        initiate_editor(self.path)

    def view(self) -> None:
        formatted = mdv.main(self.content)
        print(formatted)

    # File state change methods
    ###########################

    def rename(self, new_name: str) -> bool:
        """
        Rename a task.

        Args:
            new_name (str): the new name (forbidden characters in OS paths not allowed_

        Returns:
            (bool): Whether the file was renamed or not.
        """
        if new_name == self.name:
            return False

        new_filename = f"{new_name}{task_extension}"   # since the name will not end with .md
        new_path = os.path.join(self.prefix_path, new_filename)
        os.rename(self.path, new_path)
        self.path = new_path
        self.relative_filename = new_filename
        self.name = new_name
        return True

    def set_status(self, new_status: str) -> None:
        if new_status == self.status:
            return None

        active_statuses = [ip_str, todo_str, hold_str]
        inactive_statuses = [abandoned_str, done_str]
        if (new_status in inactive_statuses and self.status in active_statuses) or \
            (new_status in active_statuses and self.status in inactive_statuses):

            if new_status in active_statuses and self.status in inactive_statuses:
                appendage = os.pardir
            elif new_status in inactive_statuses and self.status in active_statuses:
                appendage = inactive_subdir
            else:
                raise ValueError("Conflict between new status and old status activity!")

            new_prefix_path = os.path.abspath(os.path.join(self.prefix_path, appendage))
            new_path = os.path.join(new_prefix_path, self.relative_filename)
            os.rename(self.path, new_path)
            self.prefix_path = new_prefix_path
            self.path = new_path

        self.status = new_status
        self._write_state()

    def set_effort(self, new_effort: int) -> None:
        self.effort = new_effort
        self._write_state()

    def set_importance(self, new_importance: int) -> None:
        self.importance = new_importance
        self._write_state()

    def add_flag(self, flag: str) -> None:
        if flag in self.flags:
            raise ValueError(f"Flag '{flag}' already in flags: '{self.flags}")
        else:
            self.flags += flag
        self._write_state()

    def rm_flag(self, flag: str) -> None:
        if flag in self.flags:
            self.flags.remove(flag)
        else:
            raise ValueError(f"Flag '{flag}' not in flags: '{self.flags}")
        self._write_state()

    def set_due(self, due: datetime.datetime) -> None:
        self.due = due
        self._write_state()


    # Properties
    @property
    def recurrence(self) -> tuple:
        """
        Determine recurrence and time period of recurrence

        Returns:
            tuple(bool, (int or None)): 2-tuple of the recurrence (True if recurrent) and the
            time period of recurrence (None if not recurrent).
        """
        for flag in self.flags:
            if "r" in flag:
                days_str = flag.replace("r", "").strip()
                days = int(days_str)
                return True, days
        else:
            return False, None

    @property
    def days_till_due(self) -> int:
        d = (self.due - datetime.datetime.now()).days
        return d

    @property
    def hold(self):
        return self.status == hold_str

    @property
    def done(self):
        return self.status == done_str

    @property
    def ip(self):
        return self.status == ip_str

    @property
    def todo(self):
        return self.status == todo_str

    @property
    def abandoned(self):
        return self.status == abandoned_str

    @property
    def modification_time(self):
        return os.path.getmtime(self.path)


def encode_dexcode(dexid: str, effort: int, due: datetime.datetime, importance: int, status: str, flags: list) -> str:
    """
    Create a dexcode from python objects which are easy to work with.

    Args:
        dexid (str): The dex ID (single letter followed by a number).
        effort (int): The effort, which must be in the effort_primitives.
        due (datetime.datetime): When the task is due.
        importance (int): The importance, which must be in importance_primitives
        status (str): The status string, which must be in status_primitives
        flags ([str]): All flags for this task (see dex.constants for more info)

    Returns:
        dexcode (str): The code representing all metadata about the task which otherwise can't be gathered from the
            file itself.
    """
    if effort not in effort_primitives:
        raise ValueError(f"Effort value '{effort}' not a valid effort primitive: '{effort_primitives}")
    if importance not in importance_primitives:
        raise ValueError(f"Importance value '{importance}' not a valid importance primitive: '{importance_primitives}'")
    if status not in spi_inverted:
        raise ValueError(f"Status string '{status}' not a valid status primitive: '{spi_inverted}'")
    check_flags_valid(flags)

    flags = dexcode_delimiter_flag.join(flags)
    due = due.strftime(due_date_fmt)
    return f"{ddl}{dexid}{ddm}e{effort}{ddm}d{due}{ddm}i{importance}{ddm}s{spi_inverted[status]}{ddm}f{flags}{ddr}"


def decode_dexcode(dexcode: str) -> list:
    """
    Decode a dexcode string to python objects which are convenient to work with.

    Args:
        dexcode (str): The dexcode

    Returns:
        parsed_tokens ([str, int, datetime.datetime, int, str, [str]]:
            [dexid, effort, due, importance, status, flags]

    """
    if dexcode.startswith(ddl) and \
            dexcode.endswith(ddr) and \
            dexcode.count(ddm) == 5 and \
            dexcode.count(ddl) == dexcode.count(ddr) == 1:
        tokens = dexcode.replace(ddl, "").replace(ddr, "").split(ddm)
        dexid, effort, due, importance, status, flags = tokens

        parsed_tokens = [dexid]

        for reqchar, code in [("e", effort), ("d", due), ("i", importance), ("s", status), ("f", flags)]:
            if not reqchar in code:
                raise ValueError(f"Required token '{reqchar}' not found in '{code}' token string.")

            c = code.replace(reqchar, "")

            if reqchar in ["e", "i", "s"]:
                try:
                    c = int(c)
                except ValueError:
                    raise ValueError(f"Integer value not parsable in token '{code}'")

            if reqchar == "e" and not c in effort_primitives:
                raise ValueError(f"Effort value '{c}' not a valid effort primitive: '{effort_primitives}")
            elif reqchar == "i" and not c in importance_primitives:
                raise ValueError(f"Importance value '{c}' not a valid importance primitive: '{importance_primitives}'")
            elif reqchar == "s":
                if c in spi:
                    c = spi[c]
                else:
                    raise ValueError(f"Status integer '{c}' not a valid status primitive integer: '{spi}")
            elif reqchar == "d":
                try:
                    c = datetime.datetime.strptime(c, due_date_fmt)
                except ValueError:
                    raise ValueError(f"Due date datetime object could not be decoded from '{c}'")
            elif reqchar == "f":
                c = [f for f in c.split(dexcode_delimiter_flag)]
                check_flags_valid(c)
            parsed_tokens.append(c)
        return parsed_tokens
    else:
        raise ValueError(f"Invalid dexcode format: '{dexcode}'")


def extract_dexcode_from_content(content: str) -> str:
    """
    Extract a dexcode from content. The dexcode must be on the LAST line of the content, and must be preceeded by
    ######dexcode:

    Args:
        content (str): The content in which the dexcode can be found.

    Returns:
        dexcode (str): The dexcode

    """
    id_line = content.split("\n")[-1]
    if dexcode_header in id_line:
        if all([delim in id_line for delim in (ddr, ddl, ddm)]):
            dexcode = id_line[id_line.find(ddl):id_line.find(ddr) + len(ddr)]
            return dexcode
        else:
            raise ValueError("Content missing required dexcode delimiters")
    else:
        raise ValueError("Content missing required dexcode header on first line.")


def check_flags_valid(flags: list) -> None:
    """
    Ensure list of task flags are valid

    Args:
        flags ([str]): List of flags

    Returns:
        None (throws exception if invalid)

    """
    for flag_expr in flags:
        if not any([f in flag_expr for f in flags_primitives]):
            raise ValueError(
                f"Flags strings '{flags}' not containing all valid flags primitives: '{flags_primitives}'"
            )


if __name__ == "__main__":
    d = datetime.datetime.strptime("2020-07-21", due_date_fmt)
    # print(encode_dexcode("a11", 2, d, 1, "done", ("r",)))
    # print(decode_dexcode("{[a11.e2.d2020-07-14.i1.s3.fr12&n]}"))

    # with open("/home/dude/dex/dex/example task.md", "r") as f:
    #     print(extract_dexcode_from_content(f.read()))


    # t = Task.from_file("/home/dude/dex/dex/example task.md")


    t = Task("b44", "/home/dude/dex/dex/example task2.md", 2, d, 5, "todo", ("n",), edit_content=True)
    # t.write_state()

    # t = Task.from_file("/home/dude/dex/dex/example task2.md")
    # print(t)
