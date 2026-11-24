import sys
import enum
import os
import pathlib
import stat
import subprocess
import readline
from contextlib import redirect_stdout, redirect_stderr
from collections import deque
import io
import tempfile
from app.custom_exceptions import DirectoryNotFoundException, InvalidStringException
from app.custom_exceptions import TooManyArgumentsException, UnexpectedTokenException, CompleteNotFoundException, InvalidArgumentException
#from app.logger import debug_logger
from app.stub_debug_logger import debug_logger

STDIN_FD = 0
STDOUT_FD = 1
STDERR_FD = 2
READ_FLAG = 'r'
WRITE_FLAG = 'w'
APPEND_FLAG = 'a'
complete_file = "/tmp/.URMOM"

background_jobs = [] # consists of objects of type Job

class JobStatus(enum.StrEnum):
    RUNNING = "Running"
    DONE = "Done"

class Job:
    recycled_job_numbers = deque()
    current_job_number = 1
    def __init__(self, process_id: int, command: str, status: JobStatus):
        self.job_number = Job.current_job_number
        if len(Job.recycled_job_numbers) > 0:
            self.job_number = Job.recycled_job_numbers.popleft()
        Job.current_job_number += 1
        self.process_id = process_id
        self.command = command
        self.status = status
        self.popen = None

    def add_popen(self, popen: subprocess.Popen):
        self.popen = popen

    def set_status(self, status: JobStatus):
        assert type(status) == type(JobStatus.RUNNING)
        self.status = status

    def print_job(self, stdout, recency):
        job_number_str = f"[{self.job_number}]"
        marker = '  '
        if recency == 1:
            marker = '+ '
        elif recency == 2:
            marker = '- '
        job_number_str += marker
        status_padding_length = 24
        status_str = self.status + (status_padding_length-len(self.status))*' '
        command_str = self.command
        if self.status == JobStatus.DONE:
            command_str = self.command[:len(self.command)-2]

        print(f"{job_number_str} {status_str}{command_str}", file=stdout)

    def is_done(self):
        if self.popen.poll() is not None:
            return True
        return False

class Commands:
    echo_cmd = "echo"
    exit_cmd = "exit"
    type_cmd = "type"
    pwd_cmd = "pwd"
    cd_cmd = "cd"
    jobs_cmd = "jobs"
    complete_cmd = "complete"
    history_cmd = "history"
    declare_cmd = "declare"
    null_cmd = "NULL" # used as kind of a null value, not a actual command
    available_commands = [echo_cmd, exit_cmd, type_cmd, pwd_cmd, cd_cmd, complete_cmd, jobs_cmd, history_cmd, declare_cmd]

class Signal:
    exit_signal = "exit_signal"

class Permissions:
    owner = "owner"
    other = "other"
    group = "group"
    read = "read"
    write = "write"
    executable = "executable"

original_history = []
temporary_history = []
current_history = []

def print_command_not_found(cmd):
    print(f"{cmd}: command not found", file=sys.stderr)

def print_not_found(cmd):
    print(f"{cmd}: not found", file=sys.stderr)

def print_command_fullpath(cmd, path):
    print(f"{cmd} is {path}")

def check_builtin(command) -> bool:
    if command in Commands.available_commands:
        return True
    return False

def check_file_mode(file, permission: str, level: str) -> bool:
    st = os.stat(file)
    if level == Permissions.owner and permission == Permissions.read:
        return bool(st.st_mode & stat.S_IRUSR)
    if level == Permissions.owner and permission == Permissions.write:
        return bool(st.st_mode & stat.S_IWUSR)
    if level == Permissions.owner and permission == Permissions.executable:
        return bool(st.st_mode & stat.S_IXUSR)
    if level == Permissions.group and permission == Permissions.read:
        return bool(st.st_mode & stat.S_IRGRP)
    if level == Permissions.group and permission == Permissions.write:
        return bool(st.st_mode & stat.S_IWGRP)
    if level == Permissions.group and permission == Permissions.executable:
        return bool(st.st_mode & stat.S_IXGRP)
    if level == Permissions.other and permission == Permissions.read:
        return bool(st.st_mode & stat.S_IROTH)
    if level == Permissions.other and permission == Permissions.write:
        return bool(st.st_mode & stat.S_IWOTH)
    if level == Permissions.other and permission == Permissions.executable:
        return bool(st.st_mode & stat.S_IXOTH)
    return False

def get_path_by_completer_command(command: str) -> str:
    with open(complete_file, READ_FLAG) as f:
        file_data = f.read()
        splitted = file_data.split('\n')
        debug_logger.debug(f"Reading file data for {complete_file=}")
        debug_logger.debug(f"File data (split by newline):{splitted}")
        index = splitted.index(command)
        assert index < len(splitted) - 1
        path = splitted[index + 1]
        return path

def remove_from_completer_command(command: str) -> str:
    assert command in completer_commands
    lines = []
    with open(complete_file, READ_FLAG) as f:
        file_data = f.read()
        lines = file_data.split('\n')
    index = lines.index(command)
    assert index < len(lines) - 1 # asserting that format of the file is command\npath\ncommand\npath....
    new_lines = lines[:index] + lines[index+2:]
    debug_logger.debug(f"{new_lines=}")
    with open(complete_file, WRITE_FLAG) as f:
        f.write('\n'.join(new_lines))
    index = completer_commands.index(command)
    completer_command = completer_commands[:index] + completer_commands[index+2:]

def get_all_completer_commands() -> list[str]:
    try:
        with open(complete_file, READ_FLAG) as f:
            file_data = f.read()
            splitted = file_data.split('\n')
            commands = [x for index, x in enumerate(splitted) if index % 2 == 0]
            commands = [x for x in commands if x != '']
            return commands
    except OSError as E:
        return [] 

def get_all_system_commands() -> list[str]:
    path = os.environ["PATH"]
    locations = path.split(os.pathsep)
    rv = []
    rv_set = set()
    for location in locations:
        p = pathlib.Path(location)
        if not p.is_dir():
            continue
        files = [f for f in p.iterdir()]
        for file in files:
            file_suffix = str(file).split('/')[-1]
            if file_suffix not in rv_set:
                rv.append(file_suffix)
            rv_set.add(file_suffix)
    return rv

# returns path
def get_command_path(command) -> str:
    path = os.environ["PATH"]
    locations = path.split(os.pathsep)
    for location in locations:
        p = pathlib.Path(location)
        if not p.is_dir():
            continue
        files = [f for f in p.iterdir()]
        for file in files:
            file_suffix = str(file).split('/')[-1]
            if file_suffix != command:
                continue
            if check_file_mode(file, Permissions.executable, Permissions.other):
                return str(file)
    return Commands.null_cmd

# processing complete shell command.
def process_complete(args: list):
    no_flag = None
    class Flags(enum.StrEnum):
        REGISTER_FLAG = '-C'
        PRINT_FLAG = '-p'
        REMOVE_FLAG = '-r'
    FLAGS = {Flags.REGISTER_FLAG, Flags.PRINT_FLAG, Flags.REMOVE_FLAG}
    flag_pos = {}
    flag_args = {}
    max_flag_args_count = {}
    min_flag_args_count = {}
    prev_flag = no_flag
    for flag in list(FLAGS):
        flag_pos[flag] = -float('inf')
        flag_args[flag] = []
    max_flag_args_count[Flags.REGISTER_FLAG] = 2
    min_flag_args_count[Flags.REGISTER_FLAG] = 2
    max_flag_args_count[Flags.PRINT_FLAG] = 1
    min_flag_args_count[Flags.PRINT_FLAG] = 1
    min_flag_args_count[Flags.REMOVE_FLAG] = 1
    max_flag_args_count[Flags.REMOVE_FLAG] = 1

    # throws CompleteNotFoundException if the command after -p is not found
    def perform_action_by_prevflag(flag: str):
        global complete_file
        assert flag in FLAGS
        assert min_flag_args_count[flag] <= len(flag_args[flag]) <= max_flag_args_count[flag]
        debug_logger.debug(f"complete: Performing flag action for {flag=}")
        match flag:
            case Flags.REGISTER_FLAG:
                with open(complete_file, APPEND_FLAG) as f:
                    debug_logger.debug(f"Appending {flag_args[Flags.REGISTER_FLAG][1]} to {complete_file=}")
                    f.write(f"{flag_args[Flags.REGISTER_FLAG][1]}\n")
                    debug_logger.debug(f"Appending {flag_args[Flags.REGISTER_FLAG][0]} to {complete_file=}")
                    f.write(f"{flag_args[Flags.REGISTER_FLAG][0]}\n")
                    completer_commands.append(flag_args[Flags.REGISTER_FLAG][1])
            case Flags.PRINT_FLAG:
                command_to_search_for = flag_args[Flags.PRINT_FLAG][0]
                try:
                    path = get_path_by_completer_command(command_to_search_for)
                    print(f"complete -C '{path}' {command_to_search_for}")
                except ValueError as v:
                    debug_logger.debug("VALUE ERROR")
                    raise CompleteNotFoundException(command_to_search_for) from v
            case Flags.REMOVE_FLAG:
                command_to_remove = flag_args[flag][0]
                debug_logger.debug(f"{command_to_remove=}, {completer_commands=}")
                if command_to_remove not in completer_commands:
                    return
                remove_from_completer_command(command_to_remove)
            case _:
                print(f"{flag} is the last prev flag")
    try:
        for index, arg in enumerate(args):
            debug_logger.debug(f"{index=}, {arg=}, {prev_flag=}")
            debug_logger.debug(f"{flag_args=}")
            if prev_flag is not None:
                if arg not in FLAGS and len(flag_args[prev_flag]) == max_flag_args_count[prev_flag]:
                    raise InvalidArgumentException(f"Too many arguments after {prev_flag}")
                if arg in FLAGS and len(flag_args[prev_flag]) < min_flag_args_count[prev_flag]:
                    raise InvalidArgumentException(f"Not enough arguments after {prev_flag}")
                if arg in FLAGS:
                    perform_action_by_prevflag(prev_flag)
            if arg in FLAGS:
                debug_logger.debug(f"Arg is {arg} flag")
                prev_flag = arg
                flag_pos[arg] = index
                if index > len(args) - min_flag_args_count[flag]:
                    if arg == Flags.PRINT_FLAG:
                        raise CompleteNotFoundException("")
                    elif arg == Flags.REMOVE_FLAG:
                        pass
                    else:
                        raise InvalidArgumentException(f"Must specify command scrpt path and command after the {arg} flag.")
                continue
            if prev_flag in FLAGS:
                flag_args[prev_flag].append(arg)
        debug_logger.debug(f"{flag_args=}")
        if prev_flag is not None:
            if len(flag_args[prev_flag]) > max_flag_args_count[prev_flag]:
                raise InvalidArgumentException(f"Too many arguments after {prev_flag}")
            if len(flag_args[prev_flag]) < min_flag_args_count[prev_flag]:
                raise InvalidArgumentException(f"Not enough arguments after {prev_flag}")
            perform_action_by_prevflag(prev_flag)
    except CompleteNotFoundException as comp:
        print(f"complete: {comp.args[0]}: no completion specification", file=sys.stderr)
    except InvalidArgumentException as comp:
        print(f"complete: {comp.args[0]}")

def process_cd(args: list):
    goal_path_str = ''
    try:
        if len(args) > 1:
            raise TooManyArgumentsException("Too many arguments")
        if len(args) == 0:
            return
        goal_path_str = args[0]
        if goal_path_str[0] == '/':
            goal_path = pathlib.Path(goal_path_str)
            if goal_path.is_dir():
                try:
                    os.chdir(goal_path)
                except OSError as e:
                    sys.stderr.write(f"Error: {e}")
                    sys.stderr.flush()
            else:
                print(f"{Commands.cd_cmd}: {goal_path_str}: No such file or directory", file=sys.stderr)
            return
        cwd = os.getcwd()
        goal_path_tokens = goal_path_str.split('/')
        queue = deque(goal_path_tokens)
        curr_path = pathlib.Path(cwd)
        home_path = pathlib.Path.home()
        index = -1
        while queue:
            index += 1
            current = queue.popleft()
            if index == 0 and current == "~":
                curr_path = home_path
                continue
            if current == "..":
                if str(curr_path) == '/':
                    continue
                curr_path = curr_path.parents[0]
                continue
            if current == ".":
                continue
            if current == '':
                continue
            found = False
            for f in curr_path.iterdir():
                if not f.is_dir():
                    continue
                suffix = str(f).split('/')[-1]
                if suffix == str(current):
                    curr_path = curr_path / f
                    found = True
                    break
            if found:
                continue
            raise DirectoryNotFoundException(f"{goal_path_str} is not a directory")
        os.chdir(curr_path)
    except DirectoryNotFoundException:
        print(f"cd: {goal_path_str}: No such file or directory", file=sys.stderr)
    except TooManyArgumentsException:
        print("cd: Too many arguments", file=sys.stderr)
    except OSError:
        sys.stderr.write(f"Error: {e}")
        sys.stderr.flush()

def process_echo(args: list):
    #print(f"{args=}")
    print(' '.join(args))

def process_type(args: list):
    for arg in args:
        if check_builtin(arg):
            print(f"{arg} is a shell builtin")
        else:
            path_rv = get_command_path(arg)
            if path_rv == Commands.null_cmd:
                print_not_found(arg)
            else:
                print_command_fullpath(arg, path_rv)

last_append_index = -1

def process_history(args: list):
    global last_append_index
    history = temporary_history
    n = len(history)
    num_to_show = n
    read_flag = '-r'
    write_flag = '-w'
    append_flag = '-a'
    flags = {read_flag, write_flag, append_flag}
    prev_flag = None
    for arg in args:
        if prev_flag == read_flag:
            try:
                new_history = []
                with open(arg, 'r') as f:
                    for line in f:
                        new_history.append(line.rstrip())
                    history += new_history
            except Exception as e:
                print(f"Could not read from history file: {e.args[0]}", file=sys.stderr)
        elif prev_flag == write_flag:
            try:
                with open(arg, 'w') as f:
                    f.write('\n'.join(history) + '\n')
            except Exception as e:
                print(f"Could not write to history file: {e.args[0]}", file=sys.stderr)
        elif prev_flag == append_flag:
            try:
                with open(arg, 'a') as f:
                    write_this = history[last_append_index + 1:]
                    last_append_index = len(history) - 1
                    f.write('\n'.join(write_this) + '\n')
            except Exception as e:
                print(f"Could not append to history file: {e.args[0]}", file=sys.stderr)
        elif arg in flags:
            prev_flag = arg 
        else:
            num_to_show = int(arg)
    if prev_flag is not None:
        num_to_show = 0
    num_to_show = min(num_to_show, n)
    #print(f"HISTORY: {history}")
    for i in range(n - num_to_show, n):
        print(f"{i+1} {history[i]}")

declared_variables = {}

def process_declare(args: list):
    print_flag = '-p'
    flags = {print_flag}
    prev_flag = None
    print_arg = None
    declare = {}
    for arg in args:
        if arg in flags:
            prev_flag = arg
        elif prev_flag is not None:
            if prev_flag == print_flag:
                print_arg = arg
            prev_flag = None
        else:
            splitted = arg.split('=')
            if len(splitted) != 2:
                continue
            name = splitted[0]
            val = splitted[1]
            if len(name) == 0:
                print(f"declare: `{name}={val}': not a valid identifier", file=sys.stderr)
                return
            first_char = name[0]
            if not (first_char.isalpha() or first_char == '_'):
                print(f"declare: `{name}={val}': not a valid identifier", file=sys.stderr)
                return
            for i in range(1, len(name)):
                if not (name[i].isalpha() or name[i].isdigit() or name[i] == '_'):
                    print(f"declare: `{name}={val}': not a valid identifier", file=sys.stderr)
                    return

            declare[name] = val
    for k, v in declare.items():
        declared_variables[k] = v

    if print_arg is not None:
        if print_arg not in declared_variables:
            print(f"declare: {print_arg}: not found", file=sys.stderr)
        else:
            print(f"declare -- {print_arg}=\"{declared_variables[print_arg]}\"")

def run_command(command_tokens: list, stdout, stderr, background_job: bool):
    global background_jobs
    if command_tokens == Commands.null_cmd:
        return
    command = command_tokens[0]
    args = []
    if len(command_tokens) > 1:
        args = command_tokens[1:len(command_tokens)]
        args = [arg for arg in args if arg != '']
    if background_job:
        p = subprocess.Popen(command_tokens, stdout=stdout, stderr=stderr)
        job = Job(p.pid, ' '.join(command_tokens) + ' &', JobStatus.RUNNING)
        job.add_popen(p)
        background_jobs.append(job)
        print(f"[{job.job_number}] {p.pid}")
        return
    match command:
        case Commands.exit_cmd:
            return Signal.exit_signal
        case Commands.echo_cmd:
            process_echo(args)
        case Commands.type_cmd:
            process_type(args)
        case Commands.pwd_cmd:
            cwd = os.getcwd()
            print(cwd)
        case Commands.cd_cmd:
            if len(command) == 1:
                return
            process_cd(args)
        case Commands.complete_cmd:
            process_complete(args)
        case Commands.jobs_cmd:
            removal_set = set() 
            for index, job in enumerate(background_jobs):
                most_recent = False
                recency = len(background_jobs) - index
                if job.is_done():
                    removal_set.add(job)
                    job.set_status(JobStatus.DONE)
                job.print_job(stdout, recency)
            handle_background_jobs(print_status=False)
        case Commands.history_cmd:
            process_history(args)
        case Commands.declare_cmd:
            process_declare(args)
        case _:
            cmd_path = get_command_path(command)
            if cmd_path == Commands.null_cmd:
                print_command_not_found(command)
                return
            if len(args) == 0:
                subprocess.run(command, stdout=stdout, stderr=stderr)
                return
            subprocess.run(command_tokens, stdout=stdout, stderr=stderr)

# pipelined commands: an array of command tokens
def run_pipeline(pipelined_commands):
    curr_stdin = None
    curr_stdout = None 
    n = len(pipelined_commands)
    rv = None
    last_p = None
    last_builtin = False
    last_shell_index = -1
    for i in range(n):
        if not check_builtin(pipelined_commands[i][0]):
            last_shell_index = i
    for i in range(n):
        if i == 0:
            curr_stdin = None
            curr_stdout = subprocess.PIPE
        elif i == n - 1:
            curr_stdout = sys.stdout
        else:
            curr_stdout = subprocess.PIPE
        tokens = pipelined_commands[i]
        command = tokens[0]
        if check_builtin(command):
            output = ''
            if i != n-1:
                with io.StringIO() as buf, redirect_stdout(buf):
                    rv = run_command(tokens, buf, sys.stderr, False)
                    output = buf.getvalue()
                file = tempfile.TemporaryFile()
                file.write(f'{output}'.encode())
                curr_stdin = file
                debug_logger.debug(f"Output: {output}")
            else:
                rv = run_command(tokens, sys.stdout, sys.stderr, False)
            last_builtin = True
        else:
            tokens = pipelined_commands[i]
            debug_logger.debug(f"tokens: {tokens} | stdin: {curr_stdin} | stdout: {curr_stdout}")
            if curr_stdin is not None and last_builtin:
                curr_stdin.seek(0)
                    #debug_logger.debug(f"Stdin content: {curr_stdin.read()}")
            p = subprocess.Popen(tokens, stdin=curr_stdin, stdout=curr_stdout)
            if i == last_shell_index:
                p.communicate()
            curr_stdin = p.stdout
            last_p = p
            last_builtin = False
    return rv

class TokenizedMessage:
    def __init__(self, original_message):
        self.original_message = original_message
        self.stripped = original_message.strip()
        self.tokens = []
        self.single_quote_index_pairs = []
        self.double_quote_index_pairs = []
        self.backslash_indices = []
        self.redirect_index_pairs = []
        self.invalid_message = True
        self.fd_table = {}
        self.fd_mode = {}
        self.background_job = False
        #self.tokens_before_handling_characters = []
        self.is_pipeline = False
        self.command_tokens = []
        self.command = ''
        try:
            self.single_quote_index_pairs, self.double_quote_index_pairs, self.backslash_indices = self.preprocess_quotes(self.stripped)
            self.stripped = self.expand_variables(self.single_quote_index_pairs,
                                                  self.backslash_indices, self.stripped).strip()
            self.single_quote_index_pairs, self.double_quote_index_pairs, self.backslash_indices = self.preprocess_quotes(self.stripped)

            '''
            self.tokens_before_handling_characters = self.tokenize_command(self.stripped,
                                                                    self.single_quote_index_pairs,
                                                                    self.double_quote_index_pairs)
                                                                    '''
            self.is_pipeline = self.check_pipeline(self.single_quote_index_pairs, self.double_quote_index_pairs, self.backslash_indices, self.stripped)
            if not self.is_pipeline:
                self.handle_redirections(self.stripped)
                self.handle_ampersand(self.stripped)
                self.command = self.get_command(self.stripped, self.redirect_index_pairs)
                debug_logger.debug(f"{self.command=}")
                self.command_tokens = self.tokenize_command(self.command,
                                            self.single_quote_index_pairs,
                                            self.double_quote_index_pairs)
            else:
                # tuple: (command, tokens)
                self.pipeline_command_tokens = self.get_pipeline(self.single_quote_index_pairs, self.double_quote_index_pairs, self.backslash_indices, self.stripped)
                debug_logger.debug("Pipeline command detected")
                debug_logger.debug("PIPELINE: ")
                debug_logger.debug(self.pipeline_command_tokens)
        except InvalidStringException:
            print(f"Error: Incomplete quotes detected.", file=sys.stderr)

    def expand_variables(self, single_quote_index_pairs, backslash_indices, msg):
        curr_single_index = 0
        curr_double_index = 0
        curr_backslash_index = 0
        new_msg = msg
        dollar_index = float('inf')
        dollar_index_end = float('inf')
        reading_dollar = False
        braces = True
        index_offset = 0
        index_replacements = {} # (msg_index_begin, msg_index_end, has_braces): replacement_str
        for i in range(len(msg)):
            single_left = float('inf')
            single_right = float('-inf')
            backslash_index = float('inf')
            if len(single_quote_index_pairs) > curr_single_index:
                single_left = single_quote_index_pairs[0][0]
                single_right = single_quote_index_pairs[0][1]
            if len(backslash_indices) > curr_backslash_index:
                backslash_index = backslash_indices[curr_backslash_index]
            if single_left <= i < single_right:
                continue
            if i == single_right:
                curr_single_index += 1
                continue
            if i == backslash_index + 1:
                curr_backslash_index += 1
                continue
            if reading_dollar:
                if msg[i].isalpha() or msg[i].isdigit() or msg[i] == '_':
                    continue
                if msg[i] == '{' and dollar_index == i - 1:
                    braces = True
                    continue
                dollar_index_end = i - 1
                dollar_index_begin = dollar_index + 1
                name = msg[dollar_index_begin:dollar_index_end+1]
                if braces and msg[i] == '}':
                    dollar_index_end = i
                    name = msg[dollar_index_begin+1:dollar_index_end]
                replacement = ''
                if declared_variables.get(name):
                    replacement = declared_variables[name]
                index_replacements[(dollar_index, dollar_index_end + 1)] = replacement
                reading_dollar = False
                braces = False
            elif msg[i] == '$':
                reading_dollar = True
                dollar_index = i
        if reading_dollar:
            dollar_index_end = len(msg) - 1
            dollar_index_begin = dollar_index + 1
            name = msg[dollar_index_begin:dollar_index_end + 1]
            replacement = ''
            if declared_variables.get(name):
                replacement = declared_variables[name]
            index_replacements[(dollar_index, dollar_index_end + 1)] = replacement
        new_msg = msg
        last_index = 0
        for k, replacement in index_replacements.items():
            begin, end = k
            #print(f"{begin=}, {end=}, {replacement=}")
            if last_index == 0:
                new_msg = '' 
            new_msg += msg[last_index:begin] + replacement
            last_index = end
        if last_index != len(msg) - 1 and len(index_replacements.keys()) >= 1:
            new_msg += msg[last_index:len(msg)]
        if len(index_replacements.keys()) >= 1:
            pass
            #print(f"{new_msg=}")
        return new_msg

    def get_pipeline(self, single_quote_index_pairs, double_quote_index_pairs, backslash_indices, string):
        pipeline_positions = []
        curr_backslash_index = 0
        single_quote_index = 0
        double_quote_index = 0
        for i in range(len(string)):
            curr_backslash = -2
            if curr_backslash_index < len(backslash_indices):
                curr_backslash = backslash_indices[curr_backslash_index]
            curr_single_quote_left = float('inf')
            curr_single_quote_right = -float('inf')
            if single_quote_index < len(single_quote_index_pairs):
                curr_single_quote_left = single_quote_index_pairs[single_quote_index][0]
                curr_single_quote_right = single_quote_index_pairs[single_quote_index][1]
            curr_double_quote_left = float('inf')
            curr_double_quote_right = -float('inf')
            if double_quote_index < len(double_quote_index_pairs):
                curr_double_quote_left = double_quote_index_pairs[double_quote_index][0]
                curr_double_quote_right = double_quote_index_pairs[double_quote_index][1]
            if curr_backslash + 1 == i:
                continue
            if curr_single_quote_left <= i < curr_single_quote_right:
                continue
            if curr_single_quote_right == i:
                single_quote_index += 1
                continue
            if curr_double_quote_left <= i < curr_double_quote_right:
                continue
            if curr_double_quote_right == i:
                double_quote_index += 1
                continue
            if string[i] != '|':
                continue
            pipeline_positions.append(i)
        pipeline = []
        left = 0
        for index in pipeline_positions:
            pipeline.append(string[left:index].strip())
            left = index + 1
        pipeline.append(string[left:len(string)].strip())
        pipeline_tokens = []
        for p in pipeline:
            command = self.get_command(p, [])
            single_quote, double_quote, backslashes = self.preprocess_quotes(command)
            command_tokens = self.tokenize_command(command, single_quote, double_quote)
            pipeline_tokens.append(command_tokens)
        return pipeline_tokens
    def check_pipeline(self, single_quote_index_pairs, double_quote_index_pairs, backslash_indices, string):
        curr_backslash_index = 0
        single_quote_index = 0
        double_quote_index = 0
        for i in range(len(string)):
            curr_backslash = -2
            if curr_backslash_index < len(backslash_indices):
                curr_backslash = backslash_indices[curr_backslash_index]
            curr_single_quote_left = float('inf')
            curr_single_quote_right = -float('inf')
            if single_quote_index < len(single_quote_index_pairs):
                curr_single_quote_left = single_quote_index_pairs[single_quote_index][0]
                curr_single_quote_right = single_quote_index_pairs[single_quote_index][1]
            curr_double_quote_left = float('inf')
            curr_double_quote_right = -float('inf')
            if double_quote_index < len(double_quote_index_pairs):
                curr_double_quote_left = double_quote_index_pairs[double_quote_index][0]
                curr_double_quote_right = double_quote_index_pairs[double_quote_index][1]
            if curr_backslash + 1 == i:
                continue
            if curr_single_quote_left <= i < curr_single_quote_right:
                continue
            if curr_single_quote_right == i:
                single_quote_index += 1
                continue
            if curr_double_quote_left <= i < curr_double_quote_right:
                continue
            if curr_double_quote_right == i:
                double_quote_index += 1
                continue
            if string[i] != '|':
                continue
            return True
        return False

    def handle_ampersand(self, stripped: str):
        if stripped[-2:] == ' &':
            self.stripped = stripped[:len(stripped)-2]
            self.background_job = True

    def get_command(self, stripped: str, redirect_index_pairs: list[tuple]):
        command = stripped
        if len(redirect_index_pairs) == 0:
            return command
        command = stripped[0:redirect_index_pairs[0][0]]
        return command

    def handle_redirections(self, stripped: str):
        if stripped == '':
            return
        if stripped[-1] == '>':
            raise UnexpectedTokenException("Must redirect to a file with a name")
        stripped = stripped.strip()
        further_inspect = []
        single_quote_index = 0
        double_quote_index = 0
        backslash_index = 0
        prev_character_redirect = False
        prev_character_double_redirect = False
        for index, character in enumerate(stripped):
            single_quote_left = -1
            single_quote_right = -1
            double_quote_left = -1
            double_quote_right = -1
            backslash = -1
            if len(self.backslash_indices) > backslash_index:
                backslash = self.backslash_indices[backslash_index]
            if len(self.single_quote_index_pairs) > single_quote_index:
                single_quote_left = self.single_quote_index_pairs[single_quote_index][0]
                single_quote_right = self.single_quote_index_pairs[single_quote_index][1]
            if len(self.double_quote_index_pairs) > double_quote_index:
                double_quote_left = self.double_quote_index_pairs[double_quote_index][0]
                double_quote_right = self.double_quote_index_pairs[double_quote_index][1]
            if index == backslash + 1 and backslash != -1:
                backslash_index += 1
                prev_character_redirect = False
                continue
            if double_quote_left <= index < double_quote_right:
                prev_character_redirect = False
                continue
            if index == double_quote_right:
                double_quote_index += 1
                prev_character_redirect = False
                continue
            if single_quote_left <= index < single_quote_right:
                prev_character_redirect = False
                continue
            if index == single_quote_right:
                prev_character_redirect = False
                single_quote_index += 1
                continue
            if character == '>':
                if prev_character_double_redirect:
                        raise UnexpectedTokenException("Invalid Redirect syntax: must have >> or > or << or < or |")
                if prev_character_redirect:
                    prev_character_double_redirect = True
                    further_inspect.pop()
                    further_inspect.append(index)
                    continue
                prev_character_redirect = True
                further_inspect.append(index)
                continue
            prev_character_redirect = False
            debug_logger.debug(f"{further_inspect=}")
        # format: command1 > file > file > ....
        redirect_indices = []
        redirect_fds = []
        debug_logger.debug(f"{stripped=}")
        for index in further_inspect:
            j = index - 1
            token = '>'
            original_token = token
            fd = ''
            while j >= 0:
                if stripped[j] == ' ':
                    break
                if not stripped[j].isdigit() and not stripped[j] == '>':
                    j = index-1
                    token = original_token
                    fd = '1'
                    break
                if stripped[j] != '>':
                    fd = stripped[j] + fd
                token = stripped[j] + token
                j -= 1
            if fd == '': 
                fd = '1'
            self.fd_mode[int(fd)] = WRITE_FLAG 
            if '>>' in token:
                self.fd_mode[int(fd)] = APPEND_FLAG

            debug_logger.debug(f"{token=}")
            redirect_indices.append((j+1, index)) # redirect_index[i] = left, right
            redirect_fds.append(fd)
        debug_logger.debug(f"{redirect_indices=} {len(redirect_indices)=}")
        debug_logger.debug(f"{redirect_fds=}")
        for i in range(len(redirect_indices)):
            R = redirect_indices[i][1]
            next_L = len(stripped) 
            if i+1 >= len(redirect_indices):
                next_L = len(stripped)
            else:
                next_L = redirect_indices[i+1][0]
            fd = redirect_fds[i]
            files = stripped[R+1:next_L].strip()
            debug_logger.debug(f"{files=}")
            splitted = files.split()
            self.fd_table[int(fd)] = []
            for file in splitted:
                if file == '':
                    continue
                self.fd_table[int(fd)].append(file)

        self.redirect_index_pairs = redirect_indices
        debug_logger.debug(f"{self.fd_table=}")

    def run_tokenized_command(self) -> Signal:
        if self.is_pipeline:
            rv = run_pipeline(self.pipeline_command_tokens)
            return rv

        files_stdout = []
        files_stderr = []
        stdout_flag = WRITE_FLAG 
        stderr_flag = WRITE_FLAG
        if STDOUT_FD in self.fd_table:
            files_stdout = self.fd_table[STDOUT_FD]
            if self.fd_mode[STDOUT_FD] == APPEND_FLAG:
                stdout_flag = APPEND_FLAG
        if STDERR_FD in self.fd_table:
            files_stderr = self.fd_table[STDERR_FD]
            if self.fd_mode[STDERR_FD] == APPEND_FLAG:
                stderr_flag = APPEND_FLAG

        rv = None
        if len(files_stdout) == 0 and len(files_stderr) == 0:
            rv = run_command(self.command_tokens, sys.stdout, sys.stderr, self.background_job)
        elif len(files_stdout) > 0 and len(files_stderr) == 0:
            reference_file = files_stdout[0]
            with open(reference_file, stdout_flag) as desired_stdout:
                with redirect_stdout(desired_stdout):
                    rv = run_command(self.command_tokens, desired_stdout, sys.stderr, self.background_job)
        elif len(files_stdout) == 0 and len(files_stderr) > 0:
            reference_file = files_stderr[0]
            with open(reference_file, stderr_flag) as desired_stderr:
                with redirect_stderr(desired_stderr):
                    rv = run_command(self.command_tokens, sys.stdout, desired_stderr, self.background_job)
        else:
            reference_file = files_stdout[0]
            reference_file_err = files_stderr[0]
            with open(reference_file, stdout_flag) as desired_stdout, open(reference_file_err, stderr_flag) as desired_stderr:
                with redirect_stdout(desired_stdout), redirect_stderr(desired_stderr):
                    rv = run_command(self.command_tokens, desired_stdout, desired_stderr, self.background_job)
        return rv

    def preprocess_quotes(self, stripped: str):
        if stripped == '':
            return Commands.null_cmd
        double_quote_escapable_characters = ['"', '\\', '$', '`', '\n']
        single_quoted_tokens = [] # [str_enclosed_in_singlequotes1, str_enclosed_in_singlequotes2]
        single_quote_index_pairs = [] # [(i1_1, i1_2), (i2_1, i2_2), .... (in_1, in_2)]

        single_quote_one_found = False
        last_single_quote_index = -1
        double_quoted_tokens = []
        double_quote_index_pairs = []
        double_quote_one_found = False
        last_double_quote_index = -1
        prev_character_backslash = False
        backslash_indices = []
        special_characters = {'"', "'", '\\'}
        for index, character in enumerate(stripped):
            debug_logger.debug(f"{single_quoted_tokens=}")
            debug_logger.debug(f"{single_quote_index_pairs=}")
            debug_logger.debug(f"{single_quote_one_found=}")
            debug_logger.debug(f"{double_quoted_tokens=}")
            debug_logger.debug(f"{double_quote_index_pairs=}")
            debug_logger.debug(f"{double_quote_one_found=}")
            debug_logger.debug(f"{last_single_quote_index=}")
            debug_logger.debug(f"{last_double_quote_index=}")
            debug_logger.debug(f"{prev_character_backslash=}")
            debug_logger.debug(f"{index=}, {character=}")
            debug_logger.debug(f"{backslash_indices=}")
            if character == "'" and not double_quote_one_found and not prev_character_backslash:
                if single_quote_one_found:
                    single_quote_index_pairs.append((last_single_quote_index, index))
                    single_quoted_tokens.append(stripped[last_single_quote_index: index + 1])
                    single_quote_one_found = False
                else:
                    last_single_quote_index = index
                    single_quote_one_found = True
                continue
            if character == "\\" and not single_quote_one_found and not prev_character_backslash:
                prev_character_backslash = True
                backslash_indices.append(index)
                continue
            if character == '"' and not single_quote_one_found and not prev_character_backslash:
                if double_quote_one_found:
                    double_quote_index_pairs.append((last_double_quote_index, index))
                    double_quoted_tokens.append(stripped[last_double_quote_index: index + 1])
                    double_quote_one_found = False
                else:
                    last_double_quote_index = index
                    double_quote_one_found = True
                continue
            prev_character_backslash = False
        if single_quote_one_found:
            raise InvalidStringException("Invalid Quote Scheme")
        if double_quote_one_found:
            raise InvalidStringException("Invalid Quote Scheme")
        return single_quote_index_pairs, double_quote_index_pairs, backslash_indices


    # returns tokens of a command i.e. [echo, arg1, arg2, ... argn]
    def tokenize_command(self,
                 stripped: str,
                 single_quote_index_pairs: list,
                 double_quote_index_pairs: list):
        # assumes stripped spaces i.e. 'command arg1 arg2 ...... argn'
        # with no space to left or right
        stripped = stripped.strip()
        single_quoted_tokens = []
        double_quoted_tokens = []
        for a, b in single_quote_index_pairs:
            single_quoted_tokens.append(stripped[a+1:b])
        for a,b in double_quote_index_pairs:
            double_quoted_tokens.append(stripped[a+1:b])
        debug_logger.debug("### Single quote tokens ###")
        for index, token in enumerate(single_quoted_tokens):
            debug_logger.debug(f"token {index}:")
            debug_logger.debug(token)
            debug_logger.debug(f"index: {single_quote_index_pairs[index]}")
        debug_logger.debug("### Single quote tokens finished ###")
        debug_logger.debug("### Double quote tokens finished ###")
        for index, token in enumerate(double_quoted_tokens):
            debug_logger.debug(f"token {index}:")
            debug_logger.debug(token)
            debug_logger.debug(f"index: {double_quote_index_pairs[index]}")
        debug_logger.debug("### Double quote tokens finished ###")
        last_char = ''
        curr_token = ''
        tokens = [curr_token]
        curr_token_index = 0
        curr_single_quote_index = 0
        curr_double_quote_index = 0
        unindexed_character = "no_index"
        prev_character_backslash = False
        for index, character in enumerate(stripped):
            # single quote processing
            left_single_quote_index = unindexed_character
            right_single_quote_index = unindexed_character
            if curr_single_quote_index < len(single_quote_index_pairs):
                left_single_quote_index = single_quote_index_pairs[curr_single_quote_index][0]
                right_single_quote_index = single_quote_index_pairs[curr_single_quote_index][1]
            if left_single_quote_index != unindexed_character and left_single_quote_index <= index < right_single_quote_index:
                if character != "'":
                    tokens[curr_token_index] += character
                continue
            if index == right_single_quote_index:
                curr_single_quote_index += 1
                continue
            # single quote processing done
            if character == '\\' and not prev_character_backslash:
                prev_character_backslash = True
                continue
            if prev_character_backslash:
                tokens[curr_token_index] += character
                prev_character_backslash = False
                continue
            # double quote processing
            left_double_quote_index = unindexed_character
            right_double_quote_index = unindexed_character
            if curr_double_quote_index < len(double_quote_index_pairs):
                left_double_quote_index = double_quote_index_pairs[curr_double_quote_index][0]
                right_double_quote_index = double_quote_index_pairs[curr_double_quote_index][1]
            if left_double_quote_index != unindexed_character and left_double_quote_index <= index < right_double_quote_index:
                if character != '"':
                    tokens[curr_token_index] += character
                continue
            if index == right_double_quote_index:
                curr_double_quote_index += 1
                continue
            #double quote processing done
            #escaped
            if character == ' ' and last_char == ' ':
                continue
            if character == ' ':
                curr_token_index += 1
                tokens.append('')
                continue
            tokens[curr_token_index] += character
        debug_logger.debug("Tokenized message: ")
        for token in tokens:
            debug_logger.debug(token)
        return tokens

# input: message
# output: tokens
def process_command(msg: str):
    msg = msg.strip()
    if msg == '':
        return
    tokenized_message = TokenizedMessage(msg)
    signal = tokenized_message.run_tokenized_command()
    return signal

executables = get_all_system_commands()
completer_commands = get_all_completer_commands()

# gnu readline completer function for tab completion on commands
def complete(text, state):
    options = []
    start = readline.get_begidx()
    line_buffer = readline.get_line_buffer()
    #print(f"{start=} {text=}, {state=}, {line_buffer=}")
    left_stripped_line_buffer = line_buffer.lstrip()
    space_splitted_line_buffer = left_stripped_line_buffer.split(' ')
    first_line_buffer_token = space_splitted_line_buffer[0]
    if start == len(line_buffer) - len(left_stripped_line_buffer):
        options = Commands.available_commands + list(set(executables) - set(Commands.available_commands))
        options = options + list(set(completer_commands) - set(options))
        options = [f"{option} " for option in options if option.startswith(text)]
    elif first_line_buffer_token in completer_commands:
        path = get_path_by_completer_command(first_line_buffer_token)
        argv = ['', '', '', '']
        argv[0] = path
        argv[1] = first_line_buffer_token
        argv[2] = text
        argv[3] = ''
        if len(space_splitted_line_buffer) >= 2:
            argv[3] = space_splitted_line_buffer[len(space_splitted_line_buffer) - 2] 
        COMP_LINE = "COMP_LINE"
        COMP_POINT = "COMP_POINT"
        os.environ[COMP_LINE] = line_buffer
        os.environ[COMP_POINT] = f"{len(line_buffer)}"
        result = subprocess.run(argv, stdout=subprocess.PIPE)
        decoded = result.stdout.decode('UTF-8')
        decoded = decoded[:len(decoded) - 1]
        options = decoded.split('\n')
        options = [f"{option} " for option in options if option.startswith(text)]
    else:
        p = pathlib.Path(os.getcwd())
        split = text.split('/')
        curr_path = p
        for token in split[:-1]:
            curr_path = curr_path / token
        prefix = '/'.join(split[:-1])
        if curr_path.is_dir():
            for path in curr_path.iterdir():
                name = str(path).split('/')[-1]
                if not name.startswith(split[-1]):
                    continue
                if path.is_dir():
                    if prefix != '':
                        options.append(f"{prefix}/{name}/")
                    else:
                        options.append(f"{name}/")
                else:
                    if prefix != '':
                        options.append(f"{prefix}/{name} ")
                    else:
                        options.append(f"{name} ")
    try:
        return options[state]
    except IndexError:
        return None

def handle_background_jobs(print_status=True):
    global background_jobs
    removal_set = set()
    for index, job in enumerate(background_jobs):
        if job.is_done():
            removal_set.add(job)
            job.set_status(JobStatus.DONE)
            if print_status:
                job.print_job(sys.stdout, len(background_jobs) - index)
    background_jobs = [job for job in background_jobs if job not in removal_set]
    removal_set_numbers = [job.job_number for job in removal_set]
    new_recycled = sorted(list(Job.recycled_job_numbers) + removal_set_numbers)
    curr_end_index = len(new_recycled)
    new_current_job_number = Job.current_job_number
    new_end_index =  curr_end_index
    for i in range(new_end_index - 1, -1, -1):
        if new_recycled[i] == new_current_job_number - 1:
            new_end_index = i
            new_current_job_number -= 1
            continue
        break
    Job.recycled_job_numbers = new_recycled[:new_end_index]
    Job.current_job_number = new_current_job_number

def main():
    debug_logger.debug("Started")
    # line for tab completion
    readline.parse_and_bind("tab: complete")
    readline.set_completer(complete)
    readline.set_completer_delims(' ')
    while True:
        handle_background_jobs()
        msg = input("$ ")
        try:
            signal = process_command(msg)
            temporary_history.append(msg)
            current_history.append(msg)
            if signal == Signal.exit_signal:
                try:
                    with open(os.environ[HISTFILE], APPEND_FLAG) as f:
                        f.write('\n'.join(current_history) + '\n')
                    #print(f"{temporary_history=}")
                except Exception as e:
                    pass
                return
        except Exception as e:
            print_command_not_found('')
            

HISTFILE = 'HISTFILE'

if __name__ == "__main__":
    with open(complete_file, WRITE_FLAG) as f:
        pass
    
    if HISTFILE in os.environ:
        with open(os.environ[HISTFILE], READ_FLAG) as f:
            for line in f:
                temporary_history.append(line.rstrip())
        original_history = temporary_history.copy()
    main()
