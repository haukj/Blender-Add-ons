from typing import cast
from . import ast_defs

import bpy
from bpy.types import Node, NodeSocket

from .backends.type_defs import (
    CompiledFunction,
    CompiledNodeGroup,
    DataType,
    NodeInstance,
    Operation,
    OpType,
    ValueType,
)


class Interpreter:
    def __init__(self, tree: bpy.types.NodeTree) -> None:
        self.tree = tree
        self.stack: list[ValueType | NodeSocket | list[NodeSocket]] = []
        # The nodes that we added
        self.nodes: list[Node] = []
        # Inner node trees of node groups that we have created.
        # TODO: Fill this with existing node trees in the blend file.
        self.node_group_trees: dict[str, bpy.types.NodeTree] = {}
        # Variables in the form of output sockets
        self.variables: dict[str, ValueType | NodeSocket | list[NodeSocket]] = {}
        self.function_outputs: list[NodeSocket | None] = []
        self.socket_table: dict[str, NodeSocket] = {}

    def operation(self, operation: Operation):
        op_type = operation.op_type
        op_data = operation.data
        
        # Handle ListLiteral values pushed onto the stack
        if isinstance(op_data, ast_defs.ListLiteral):
            evaluated_elements = []
            for e in op_data.elements:
                self.operation(Operation(OpType.PUSH_VALUE, e))
                evaluated_elements.append(self.stack.pop())
            self.stack.append(evaluated_elements)
            return
        
        assert (
            OpType.END_OF_STATEMENT.value == 11
        ), "Exhaustive handling of Operation types."
        if op_type == OpType.PUSH_VALUE:
            self.stack.append(op_data)
        elif op_type == OpType.CREATE_VAR:
            assert isinstance(op_data, str), "Variable name should be a string."
            socket = self.stack.pop()
            while isinstance(socket, ast_defs.Name):
                var_id = socket.id
                socket = self.variables.get(var_id, socket)
            assert isinstance(
                socket, (NodeSocket, list, int)
            ), "Create var expects a node socket or struct or loop index."
            if isinstance(socket, list):
                socket = cast(list[NodeSocket], socket)
            self.variables[op_data] = socket
        elif op_type == OpType.GET_VAR:
            assert isinstance(op_data, str), "Variable name should be a string."
            self.stack.append(self.variables[op_data])
        elif op_type == OpType.GET_OUTPUT:
            assert isinstance(op_data, int), "Bug in type checker, index should be int."
            index = op_data
            struct = self.stack.pop()
            assert isinstance(
                struct, list
            ), "Bug in type checker, GET_OUTPUT only works on structs."
            # Index order is reversed
            self.stack.append(struct[-index - 1])
        elif op_type == OpType.SET_OUTPUT:
            assert isinstance(op_data, tuple), "Data should be tuple of index and value"
            index, value = op_data
            self.nodes[-1].outputs[index].default_value = value  # type: ignore
        elif op_type == OpType.SET_FUNCTION_OUT:
            assert isinstance(op_data, int), "Data should be an index"
            socket = self.stack.pop()
            assert isinstance(socket, NodeSocket)
            self.function_outputs[op_data] = socket
        elif op_type == OpType.SPLIT_STRUCT:
            struct = self.stack.pop()
            assert isinstance(
                struct, list
            ), "Bug in type checker, GET_OUTPUT only works on structs."
            self.stack += struct
        elif op_type == OpType.CALL_FUNCTION:
            assert isinstance(op_data, CompiledFunction), "Bug in type checker."
            args = self.get_args(self.stack, len(op_data.inputs))
            # Store state outside function, and prepare state in function
            outer_vars = self.variables
            self.variables = {}
            for name, arg in zip(op_data.inputs, args):
                self.variables[name] = arg
            outer_function_outputs = self.function_outputs
            self.function_outputs = [None for _ in range(op_data.num_outputs)]
            outer_stack = self.stack
            self.stack = []
            # Execute function
            for operation in op_data.body:
                self.operation(operation)
            # Restore state outside function
            self.stack = outer_stack
            if len(self.function_outputs) == 1:
                output = self.function_outputs[0]
                assert isinstance(output, NodeSocket)
                self.stack.append(output)
            elif len(self.function_outputs) > 1:
                for output in self.function_outputs:
                    assert isinstance(output, NodeSocket)
                self.stack.append(list(reversed(self.function_outputs)))  # type: ignore
            self.function_outputs = outer_function_outputs
            self.variables = outer_vars
        elif op_type == OpType.CALL_NODEGROUP:
            assert isinstance(op_data, CompiledNodeGroup), "Bug in type checker."
            args = self.get_args(self.stack, len(op_data.inputs))
            self.execute_node_group(op_data, args)
        elif op_type == OpType.CALL_BUILTIN:
            assert isinstance(op_data, NodeInstance), "Bug in compiler."
            args = self.get_args(self.stack, len(op_data.inputs))
            node = self.add_builtin(
                op_data,
                args,
            )
            outputs = op_data.outputs
            if len(outputs) == 1:
                socket = node.outputs[outputs[0]]
                self.socket_table[str(socket)] = socket
                self.stack.append(socket)
            elif len(outputs) > 1:
                self.stack.append([node.outputs[o] for o in reversed(outputs)])
            self.nodes.append(node)
        elif op_type == OpType.RENAME_NODE:
            self.nodes[-1].label = op_data
        elif op_type == OpType.END_OF_STATEMENT:
            self.stack = []
        elif op_type == OpType.PACK_LIST:
            assert isinstance(op_data, int), "PACK_LIST expects an integer count."
            elements = [self.stack.pop() for _ in range(op_data)]
            elements.reverse()
            self.stack.append(elements)
        else:
            print(f"Need implementation of {op_type}")
            raise NotImplementedError

    def get_args(self, stack: list, num_args: int) -> list[ValueType]:
        if num_args == 0:
            return []
        args = stack[-num_args:]
        stack[:] = stack[:-num_args]
        return args

    def add_builtin(
        self, node_info: NodeInstance, args: list[ValueType]
    ) -> bpy.types.Node:
        tree = self.tree
        node = tree.nodes.new(type=node_info.key)
        for name, value in node_info.props:
            setattr(node, name, value)
        for i, input_index in enumerate(node_info.inputs):
            arg = args[i]
            if isinstance(arg, bpy.types.NodeSocket):
                tree.links.new(arg, node.inputs[input_index])
            elif isinstance(arg, list):
                # Special handling for Join Geometry multi-input
                if node.bl_idname == 'GeometryNodeJoinGeometry':
                    print("JOIN GEOMETRY DEBUG:")
                    print(f"  - Node: {node}")
                    print(f"  - Args: {args}")
                    print(f"  - Inputs: {[i.name for i in node.inputs]}")
                    for geo_input in reversed(arg):
                        print(f"    - geo_input: {geo_input} ({type(geo_input)})")
                        # Resolve Name references if necessary
                        if isinstance(geo_input, ast_defs.Name):
                            geo_input = self.variables.get(geo_input.id, geo_input)
                        socket = self.get_output_socket(geo_input)
                        print(f"    → socket from get_output_socket: {socket} ({type(socket)})")
                        if isinstance(socket, bpy.types.NodeSocket):
                            self.tree.links.new(socket, node.inputs["Geometry"])
                else:
                    input_socket = node.inputs[input_index]
                    if isinstance(input_socket, bpy.types.NodeSocketGeometry):
                        if hasattr(input_socket, "is_multi_input") and input_socket.is_multi_input:
                            for geo_input in arg:
                                if isinstance(geo_input, ast_defs.Name):
                                    geo_input = self.variables.get(geo_input.id, geo_input)
                                socket = self.get_output_socket(geo_input)
                                if isinstance(socket, bpy.types.NodeSocket):
                                    self.tree.links.new(socket, input_socket)
                        elif isinstance(arg[0], bpy.types.NodeSocket):
                            self.tree.links.new(arg[0], input_socket)
            else:
                input_socket = node.inputs[input_index]
                if arg is not None:
                    input_socket.default_value = arg  # type: ignore
        return node

    def get_output_socket(self, value):
        if isinstance(value, list):
            return [self.get_output_socket(v) for v in value]
        if str(value) in self.socket_table:
            return self.socket_table[str(value)]
        if hasattr(value, "socket"):
            return value.socket
        return value

    @staticmethod
    def data_type_to_socket_type(dtype: DataType) -> str:
        if dtype == DataType.BOOL:
            return "NodeSocketBool"
        elif dtype == DataType.INT:
            return "NodeSocketInt"
        elif dtype == DataType.FLOAT:
            return "NodeSocketFloat"
        elif dtype == DataType.RGBA:
            return "NodeSocketColor"
        elif dtype == DataType.VEC3:
            return "NodeSocketVector"
        elif dtype == DataType.GEOMETRY:
            return "NodeSocketGeometry"
        elif dtype == DataType.STRING:
            return "NodeSocketString"
        elif dtype == DataType.SHADER:
            return "NodeSocketShader"
        elif dtype == DataType.OBJECT:
            return "NodeSocketObject"
        elif dtype == DataType.IMAGE:
            return "NodeSocketImage"
        elif dtype == DataType.COLLECTION:
            return "NodeSocketCollection"
        elif dtype == DataType.TEXTURE:
            return "NodeSocketTexture"
        elif dtype == DataType.MATERIAL:
            return "NodeSocketMaterial"
        elif dtype == DataType.ROTATION:
            return "NodeSocketRotation"
        else:
            assert False, "Unreachable"

    def execute_node_group(self, node_group: CompiledNodeGroup, args: list[ValueType]):
        if node_group.name in self.node_group_trees:
            node_tree = self.node_group_trees[node_group.name]
        else:
            # Create the node group's inner tree:
            node_tree = bpy.data.node_groups.new(node_group.name, self.tree.bl_idname)

            for input in node_group.inputs:
                in_socket = node_tree.interface.new_socket(
                    input.name,
                    in_out="INPUT",
                    socket_type=self.data_type_to_socket_type(input.dtype),
                )
                if input.value is not None:
                    in_socket.default_value = input.value  # type: ignore
            for output in node_group.outputs:
                out_socket = node_tree.interface.new_socket(
                    output.name,
                    in_out="OUTPUT",
                    socket_type=self.data_type_to_socket_type(output.dtype),
                )
                if output.value is not None:
                    out_socket.default_value = output.value  # type: ignore
            group_input = node_tree.nodes.new("NodeGroupInput")
            group_output = node_tree.nodes.new("NodeGroupOutput")

            # Store state outside node group, and prepare state in node group
            outer_tree = self.tree
            self.tree = node_tree
            outer_vars = self.variables
            self.variables = {}
            for socket in group_input.outputs:
                self.variables[socket.name] = socket
            outer_function_outputs = self.function_outputs
            self.function_outputs = [None for _ in range(len(node_group.outputs))]
            outer_stack = self.stack
            self.stack = []
            # Execute node group
            for operation in node_group.body:
                self.operation(operation)

            # Connect to the group outputs
            for index, foutput in enumerate(self.function_outputs):
                if isinstance(foutput, NodeSocket):
                    node_tree.links.new(foutput, group_output.inputs[index])
                elif foutput is not None:
                    group_output.inputs[index].default_value = foutput  # type: ignore

            # Restore state outside node group
            self.stack = outer_stack
            self.function_outputs = outer_function_outputs
            self.variables = outer_vars
            self.tree = outer_tree
            # Store it so we don't recreate it if called multiple times.
            self.node_group_trees[node_group.name] = node_tree

        # Add the group and connect the arguments
        group_name = (
            "GeometryNodeGroup"
            if self.tree.bl_idname == "GeometryNodeTree"
            else "ShaderNodeGroup"
        )
        node = self.tree.nodes.new(group_name)
        node = cast(bpy.types.NodeGroup, node)
        node.node_tree = node_tree
        for i, arg in enumerate(args):
            if isinstance(arg, NodeSocket):
                self.tree.links.new(arg, node.inputs[i])
            elif arg is not None:
                node.inputs[i].default_value = arg  # type: ignore
        self.nodes.append(node)

        if len(node.outputs) == 1:
            self.stack.append(node.outputs[0])
        elif len(node.outputs) > 1:
            self.stack.append(
                [node.outputs[i] for i in reversed(range(len(node.outputs)))]
            )
