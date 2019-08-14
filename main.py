import platform
import os
import subprocess
import re
import sys
import uuid
import shutil

from templates import *

if sys.version_info[0] < 3:
    raise Exception('Only Python 3+ is supported')


class DLLExport:
    __slots__ = ('ordinal', 'hint', 'rva', 'mangled_name', 'name')

    def __init__(self, ordinal, hint, rva, mangled_name, name):
        self.ordinal = ordinal
        self.hint = hint
        self.rva = rva
        self.mangled_name = mangled_name
        self.name = name

    def __str__(self):
        n = self.name if self.name else self.mangled_name
        return 'DLLExport: %s %s %s' % (self.ordinal, self.rva, n)


import argparse
parser = argparse.ArgumentParser()
parser.add_argument('target', help='The filepath to the target DLL file')
args = parser.parse_args()


target_fp = args.target.replace('/', os.sep)
target_name = target_fp.rsplit(os.sep, 1)[-1].replace('.dll', '')

print('Target:', target_fp)


if platform.machine().endswith('64'):
    program_files = os.environ['ProgramFiles(x86)']
else:
    program_files = os.environ['ProgramFiles']

basedir = program_files
versions = ['11.0', '12.0', '14.0']
filepath = os.path.join('Common7', 'Tools', 'VsDevCmd.bat')
tool_info = []
tool_path = None

for version in versions:
    msvc_tools = os.path.join(basedir, 'Microsoft Visual Studio ' + version, filepath)
    if os.path.exists(msvc_tools) and os.path.isfile(msvc_tools):
        print('Found MSVC %s' % version)
        tool_info.append((version, msvc_tools))


basedir = os.path.join(program_files, 'Microsoft Visual Studio')
if os.path.exists(basedir) and os.path.isdir(basedir):
    versions = ['2017', '2019']
    edition = 'Community'
    for version in versions:
        msvc_tools = os.path.join(basedir, version, edition, filepath)
        if os.path.exists(msvc_tools) and os.path.isfile(msvc_tools):
            print('Found MSVC %s' % version)
            tool_info.append((version, msvc_tools))


def select_version():
    global tool_path, tool_info
    dialog = 'Select one of the following MSVC tools:\n'
    for i, t in enumerate(tool_info):
        msvc_ver, fpath = t
        dialog += '    %s: MSVC %s (%s)\n' % (i, msvc_ver, fpath)
    selection = input(dialog)

    try:
        selection = int(selection)
        if not 0 <= selection < len(tool_info):
            raise ValueError
    except ValueError:
        select_version()
    else:
        tool_path = tool_info[selection][1]


if len(tool_info) != 0:
    select_version()
else:
    print('MSVC is not found.')
    sys.exit(1)


def run_msvc_cmd(*args):
    global tool_path
    cmd = subprocess.Popen('cmd.exe', stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    cmd_in = '"%s"\n' % tool_path
    for arg in args:
        cmd_in += str(arg)
        cmd_in += '\n'
    cmd_in = cmd_in.encode('utf-8')
    cmd_out, cmd_err = cmd.communicate(cmd_in)
    cmd_out = cmd_out.decode('utf-8')
    return cmd_out


print('Dumping exports...')
export_dump = run_msvc_cmd('dumpbin /EXPORTS %s' % target_fp)


EXPORT_REGEX = re.compile('(ordinal +hint +RVA +name\r\n\r\n)(( +[0-9]+ +[0-9A-Z]+ +[0-9A-Z]+ +[a-zA-z0-9@$?_]+\r\n)+)')
UNMANGLE_REGEX = re.compile('is :- \"(.+)\"')
match = EXPORT_REGEX.search(export_dump)
if match is None:
    print('Could not get exports.')
    sys.exit(1)

export_dump = match.group(2).split('\r\n')

while '' in export_dump:
    export_dump.remove('')

exports = []
unmangle_cmd = ''
max_ordinal = 0

for dump in export_dump:
    ordinal, hint, rva, mangled_name = ' '.join(dump.split()).split()
    max_ordinal = max(int(ordinal), max_ordinal)
    name = None
    unmangle_cmd += 'undname %s\n' % mangled_name
    exports.append(DLLExport(ordinal, hint, rva, mangled_name, name))


if unmangle_cmd:
    print('Demangling export names...')
    i = 0
    for match in UNMANGLE_REGEX.finditer(run_msvc_cmd(unmangle_cmd)):
        exports[i].name = match.group(1)
        print(exports[i])
        i += 1


def generate_project_guid():
    return str(uuid.uuid4()).upper()


project_name = target_name
solution_name = project_name

solution_dir = os.path.join('.', 'build')
project_dir = os.path.join(solution_dir, project_name)

if os.path.exists(solution_dir):
    shutil.rmtree(solution_dir)

os.mkdir(solution_dir)

if not os.path.exists(project_dir):
    os.mkdir(project_dir)

project_guid = generate_project_guid()

cpp_filename = target_name + '_proxy.cpp'
def_filename = target_name + '_proxy.def'

sln_file = open(os.path.join(solution_dir, solution_name + '.sln'), 'w+')
sln_file.write(SOLUTION_TEMPLATE.format(project_type_guid=CPP_PROJECT_TYPE_GUID, project_name=project_name, project_guid=project_guid))
sln_file.close()

project_file = open(os.path.join(project_dir, project_name + '.vcxproj'), 'w+')
project_file.write(VCPROJECT_TEMPLATE.format(project_guid=project_guid, project_name=project_name, project_name_upper=project_name.upper(), cpp_filename=cpp_filename, def_filename=def_filename))
project_file.close()

project_filter_file = open(os.path.join(project_dir, project_name + '.vcxproj' + '.filters'), 'w+')
project_filter_file.write(VCPROJECT_FILTER_TEMPLATE.format(source_filter_uuid=generate_project_guid(), header_filter_uuid=generate_project_guid(), resource_filter_uuid=generate_project_guid(), cpp_filename=cpp_filename, def_filename=def_filename))
project_filter_file.close()

project_user_file = open(os.path.join(project_dir, project_name + '.vcxproj' + '.user'), 'w+')
project_user_file.write(VCPROJECT_USER_TEMPLATE)
project_user_file.close()

cpp_file = open(os.path.join(project_dir, cpp_filename), 'w+')
def_file = open(os.path.join(project_dir, def_filename), 'w+')

proc_addresses = '\n'.join([GET_ADDRESS_TEMPLATE % (export.ordinal, export.mangled_name) for export in exports])
code = CPP_TEMPLATE % (max_ordinal, target_fp, proc_addresses)

export_defs = ''

for export in exports:
    ordinal = export.ordinal
    func_name = '__E__%s__' % ordinal

    if export.name == export.mangled_name:
        code += FUNC_TEMPLATE_C % (func_name, ordinal)
        export_defs += '%s=%s @%s\n' % (export.name, func_name, ordinal)
    else:
        code += FUNC_TEMPLATE_CPP % (func_name, ordinal)
        export_defs += '%s=%s @%s\n' % (export.mangled_name, '_' + func_name + '@0', ordinal)

def_code = DEF_TEMPLATE % (target_name, export_defs)

cpp_file.write(code)
cpp_file.close()
def_file.write(def_code)
def_file.close()

