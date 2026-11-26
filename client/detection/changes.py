from collections import defaultdict


def detect_local_renames(current: dict, previous: dict) -> list:
    added_by_hash = defaultdict(list)
    deleted_by_hash = defaultdict(list)

    for path, info in current.items():
        if path not in previous:
            added_by_hash[info["hash"]].append(path)

    for path, info in previous.items():
        if path not in current:
            deleted_by_hash[info["hash"]].append(path)

    renames = []

    for content_hash, old_paths in deleted_by_hash.items():
        new_paths = added_by_hash.get(content_hash, [])

        if len(old_paths) == 1 and len(new_paths) == 1:
            renames.append((old_paths[0], new_paths[0]))

    return renames
