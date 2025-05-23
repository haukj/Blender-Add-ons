import os
import pickle

import bpy

from .backends.type_defs import FileData
from .compiler import Compiler
from .mf_parser import Error

extension_dir = bpy.utils.extension_path_user(__package__, create=True)
custom_implementations_dir = bpy.utils.extension_path_user(
    __package__, path="custom_implementations", create=True
)
# This is the default standard library.
# It can change between updates and should not be edited by the user,
# so we keep it separate.
default_implementations_dir = os.path.join(
    os.path.dirname(os.path.realpath(__file__)), "custom_implementations"
)
file_data = FileData()


@bpy.app.handlers.persistent  # type: ignore
def load_custom_implementations(
    dummy, dir: str = "", force_update: bool = False
) -> list[tuple[str, list[Error]]]:
    if dir == "" or dir is None:
        prefs = bpy.context.preferences.addons[__package__].preferences
        dir = prefs.custom_implementations_folder  # type:ignore
    # Load the default standard library first.
    filepaths = [
        os.path.join(default_implementations_dir, filename)
        for filename in sorted(os.listdir(default_implementations_dir))
    ]
    # Now we load the custom implementations
    # so that they can overwrite defaults if wanted.
    filepaths.extend(
        [os.path.join(dir, filename) for filename in sorted(os.listdir(dir))]
    )
    errors: list[tuple[str, list[Error]]] = []
    file_data.geometry_nodes = {}
    file_data.shader_nodes = {}
    if not force_update:
        for filepath in filepaths:
            filename = os.path.basename(filepath)
            if filename.startswith("cache"):
                with open(filepath, "rb") as f:
                    cached = pickle.load(f)
                    if filename.endswith("_gn"):
                        file_data.geometry_nodes = cached
                    elif filename.endswith("_sh"):
                        file_data.shader_nodes = cached
                    f.close()
        return errors
    geo_compiler = Compiler("GeometryNodeTree")
    sha_compiler = Compiler("ShaderNodeTree")
    for filepath in filepaths:
        filename = os.path.basename(filepath)
        if not filename.startswith("cache"):
            with open(filepath, "r") as f:
                source = f.read()
                if filename.endswith("_gn") or not filename.endswith("_sh"):
                    succeeded = geo_compiler.check_functions(source)
                    if not succeeded:
                        errors.append((filename, geo_compiler.errors))
                if filename.endswith("_sh") or not filename.endswith("_gn"):
                    succeeded = sha_compiler.check_functions(source)
                    if not succeeded:
                        errors.append((filename, sha_compiler.errors))
                f.close()
    if errors != []:
        return errors
    # Store everything if succesful.
    file_data.geometry_nodes = geo_compiler.type_checker.functions
    file_data.shader_nodes = sha_compiler.type_checker.functions
    # Add to cache
    with open(os.path.join(dir, "cache_gn"), "w+b") as f:
        pickle.dump(file_data.geometry_nodes, f)
        f.close()
    with open(os.path.join(dir, "cache_sh"), "w+b") as f:
        pickle.dump(file_data.shader_nodes, f)
        f.close()
    return errors


class MF_OT_load_custom_implementations(bpy.types.Operator):
    """Load all the custom implementations from the given folder.
    If there is a cache it will be loaded from there instead,
    unless `force_update` is true"""

    bl_idname = "node.mf_load_custom_implementations"
    bl_label = "Load custom implementations"
    bl_options = {"REGISTER", "UNDO"}

    force_update: bpy.props.BoolProperty(  # type: ignore
        name="Force Update",
        description="Force reparsing of files, even if there is a cache file",
        default=False,
    )

    def execute(self, context: bpy.types.Context):
        prefs = context.preferences.addons[__package__].preferences
        errors = load_custom_implementations(
            None, prefs.custom_implementations_folder, self.force_update  # type: ignore
        )
        if errors != []:
            self.report(
                {"ERROR"}, "Errors when loading macros. See console for more details."
            )
            for filename, file_errors in errors:
                print(f"Errors in {filename}")
                for file_error in file_errors:
                    print(file_error)
            # Ensure that we don't use wrong implementations
            file_data.geometry_nodes = {}
            file_data.shader_nodes = {}
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Succesfully loaded {file_data.num_funcs()} custom implementations.",
        )
        return {"FINISHED"}


classes = (MF_OT_load_custom_implementations,)


def register():
    global file_data
    file_data = FileData()
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(load_custom_implementations)  # type: ignore


def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)
