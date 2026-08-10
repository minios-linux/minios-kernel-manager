#!/usr/bin/env python3

import io
import json
import os
import stat
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def invoke_main(arguments, patches):
    import minios_kernel

    stdout = io.StringIO()
    stderr = io.StringIO()
    contexts = [
        patch.object(sys, 'argv', ['minios-kernel'] + arguments),
        patch.object(sys, 'stdout', stdout),
        patch.object(sys, 'stderr', stderr),
        patch('minios_kernel.os.geteuid', return_value=0),
    ] + patches
    entered = []
    try:
        for context in contexts:
            entered.append(context)
            context.__enter__()
        result = minios_kernel.main()
        return result, stdout.getvalue(), stderr.getvalue()
    finally:
        for context in reversed(entered):
            context.__exit__(None, None, None)


def records_from(output):
    lines = output.splitlines()
    assert lines
    records = [json.loads(line) for line in lines]
    assert all(record.get('type') for record in records)
    assert all(
        line == json.dumps(record, ensure_ascii=False, separators=(',', ':'))
        for line, record in zip(lines, records))
    return records


def test_list_is_one_compact_typed_ndjson_record():
    _, stdout, stderr = invoke_main(['--json', 'list'], [
        patch('minios_kernel.find_minios_directory', return_value='/minios'),
        patch('minios_kernel.list_all_kernels', return_value=['old', 'new']),
        patch('minios_kernel.get_active_kernel', return_value='new'),
        patch('minios_kernel.is_kernel_currently_running',
              side_effect=lambda version: version == 'old'),
    ])

    records = records_from(stdout)
    assert len(records) == 1
    assert records[0]['type'] == 'list'
    assert records[0]['success'] is True
    assert stderr == ''


def test_mutation_failure_is_nonzero_ndjson_with_human_stderr():
    with pytest.raises(SystemExit) as exit_info:
        invoke_main(['activate', 'new', '--json'], [
            patch('minios_kernel.find_minios_directory', return_value='/minios'),
            patch('minios_kernel.list_all_kernels', return_value=['old', 'new']),
            patch('minios_kernel.get_active_kernel', return_value='old'),
            patch('minios_kernel.activate_kernel', return_value=False),
        ])

    # Run the command function directly as well so its separated streams can be
    # asserted after the expected SystemExit.
    stdout = io.StringIO()
    stderr = io.StringIO()
    args = SimpleNamespace(
        json=True, json_stream=stdout, kernel_version='new')
    with patch.object(sys, 'stdout', stderr), \
         patch.object(sys, 'stderr', stderr), \
         patch('minios_kernel.find_minios_directory', return_value='/minios'), \
         patch('minios_kernel.list_all_kernels', return_value=['old', 'new']), \
         patch('minios_kernel.get_active_kernel', return_value='old'), \
         patch('minios_kernel.activate_kernel', return_value=False), \
         pytest.raises(SystemExit) as direct_exit:
        from minios_kernel import activate_kernel_cmd
        activate_kernel_cmd(args)

    assert exit_info.value.code == 1
    assert direct_exit.value.code == 1
    record = records_from(stdout.getvalue())[0]
    assert record['type'] == 'error'
    assert record['success'] is False
    assert 'Failed to activate' in stderr.getvalue()


def test_privilege_failure_keeps_record_stdout_and_diagnostic_stderr():
    import minios_kernel

    stdout = io.StringIO()
    stderr = io.StringIO()
    with patch.object(sys, 'argv', ['minios-kernel', '--json', 'list']), \
         patch.object(sys, 'stdout', stdout), \
         patch.object(sys, 'stderr', stderr), \
         patch('minios_kernel.os.geteuid', return_value=1000), \
         pytest.raises(SystemExit) as exit_info:
        minios_kernel.main()

    assert exit_info.value.code == 1
    record = records_from(stdout.getvalue())[0]
    assert record['type'] == 'error'
    assert record['command'] == 'privilege'
    assert 'root privileges' in stderr.getvalue()


def test_package_progress_and_result_are_ndjson_and_logs_are_stderr(tmp_path):
    import minios_kernel

    workspace_parent = tmp_path / 'workspace-parent'
    workspace_parent.mkdir()
    workspace_parent.chmod(0o700)
    output = tmp_path / 'output'
    stdout = io.StringIO()
    stderr = io.StringIO()
    observed_workspace = {}

    def copy_vmlinuz(version, temp_dir, output_dir):
        workspace_stat = os.lstat(temp_dir)
        observed_workspace['uid'] = workspace_stat.st_uid
        observed_workspace['mode'] = stat.S_IMODE(workspace_stat.st_mode)
        path = os.path.join(output_dir, 'vmlinuz-' + version)
        open(path, 'wb').write(b'kernel')
        return path

    def create_squashfs(version, compression, output_dir, **kwargs):
        path = os.path.join(output_dir, '01-kernel-' + version + '.sb')
        open(path, 'wb').write(b'squashfs')
        return path

    def generate_initramfs(version, output_dir, **kwargs):
        path = os.path.join(output_dir, 'initrfs-' + version + '.img')
        open(path, 'wb').write(b'initrd')
        return path

    args = SimpleNamespace(
        json=True, json_stream=stdout, output=str(output),
        temp_dir=str(workspace_parent), repo='linux-image-test', deb=None,
        force_update=False, sqfs_comp='zstd')
    with patch.object(sys, 'stdout', stderr), \
         patch.object(sys, 'stderr', stderr), \
         patch('minios_kernel.download_kernel_package', return_value='test'), \
         patch('minios_kernel.copy_vmlinuz', side_effect=copy_vmlinuz), \
         patch('minios_kernel.create_squashfs_image', side_effect=create_squashfs), \
         patch('minios_kernel.generate_initramfs', side_effect=generate_initramfs), \
         patch('minios_kernel.get_last_kernel_versions',
               return_value={'actual_version': 'test'}), \
         patch('minios_kernel.find_minios_directory', return_value=None):
        minios_kernel.package_kernel(args)

    records = records_from(stdout.getvalue())
    assert records[-1]['type'] == 'result'
    assert records[-1]['success'] is True
    assert any(record['type'] == 'progress' for record in records)
    assert observed_workspace == {'uid': os.geteuid(), 'mode': 0o700}
    assert 'Created temporary directory' in stderr.getvalue()
    assert {path.name for path in output.iterdir()} == {
        '01-kernel-test.sb', 'vmlinuz-test', 'initrfs-test.img'}
