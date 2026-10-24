"""The stand-in engine's (see app.py) functions: Studio's adapters wrap them when they install; none of them runs."""


def refuses(name):
    def stand_in(*args, **kwargs):
        raise RuntimeError(f"The smoke test's stand-in engine has no {name}.")
    stand_in.__name__ = name.rsplit(".", 1)[-1]
    return stand_in
