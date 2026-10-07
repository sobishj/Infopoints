from app.rootmap import container_to_host, host_to_container, normalize_host_path, parse_roots, root_alias


def test_aliases():
    assert root_alias("C:\\") == "c"
    assert root_alias("D:\\Data\\Projects") == "d_data_projects"
    assert root_alias("\\\\fileserver\\projects") == "unc_fileserver_projects"
    assert root_alias("/mnt/shares") == "mnt_shares"


def test_duplicate_aliases_are_made_unique():
    roots = parse_roots("C:\\a_b, C:\\a\\b")
    assert len({r.alias for r in roots}) == 2


def test_windows_drive_mapping_is_case_insensitive():
    roots = parse_roots("C:\\")
    assert host_to_container("C:\\Users\\sobis\\Downloads", roots) == "/roots/c/Users/sobis/Downloads"
    assert host_to_container("c:/Users/sobis/Downloads", roots) == "/roots/c/Users/sobis/Downloads"
    assert host_to_container("C:\\", roots) == "/roots/c"


def test_most_specific_root_wins():
    roots = parse_roots("D:\\, D:\\Data")
    assert host_to_container("D:\\Data\\x", roots) == "/roots/d_data/x"


def test_unc_and_posix():
    roots = parse_roots("\\\\srv\\share,/data")
    assert host_to_container("\\\\srv\\share\\Team\\A", roots) == "/roots/unc_srv_share/Team/A"
    assert host_to_container("/data/proj", roots) == "/roots/data/proj"


def test_paths_outside_roots_and_traversal_are_rejected():
    roots = parse_roots("C:\\Docs")
    assert host_to_container("C:\\Windows", roots) is None
    assert host_to_container("C:\\Docs\\..\\Windows", roots) is None
    assert host_to_container("D:\\Docs", roots) is None
    assert host_to_container("/etc", roots) is None


def test_round_trip():
    roots = parse_roots("C:\\, \\\\srv\\share, /data")
    for host in ("C:\\Users\\x\\Downloads", "\\\\srv\\share\\a\\b", "/data/p/q", "C:\\"):
        assert container_to_host(host_to_container(host, roots), roots) == normalize_host_path(host)
