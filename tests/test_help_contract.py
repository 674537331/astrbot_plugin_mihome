import ast
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "main.py"
METADATA_PATH = ROOT / "metadata.yaml"


def decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        return decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        prefix = decorator_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


class HelpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
        cls.plugin_class = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MiHomeControlPlugin"
        )

    def _command_handlers(self):
        for node in self.plugin_class.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            command_decorators = [
                decorator
                for decorator in node.decorator_list
                if decorator_name(decorator) == "filter.command"
            ]
            if command_decorators:
                yield node, command_decorators[0]

    def test_all_chat_commands_have_help_descriptions(self):
        missing = [
            node.name
            for node, _ in self._command_handlers()
            if not (ast.get_docstring(node) or "").strip()
        ]
        self.assertEqual(missing, [])

    def test_mihome_help_is_public_and_has_english_aliases(self):
        handler, command_decorator = next(
            (node, decorator)
            for node, decorator in self._command_handlers()
            if ast.literal_eval(decorator.args[0]) == "米家帮助"
        )

        decorator_names = {decorator_name(item) for item in handler.decorator_list}
        self.assertNotIn("filter.permission_type", decorator_names)

        alias_node = next(
            keyword.value
            for keyword in command_decorator.keywords
            if keyword.arg == "alias"
        )
        aliases = ast.literal_eval(alias_node)
        self.assertTrue({"mihome_help", "mihomehelp"}.issubset(aliases))

    def test_runtime_and_metadata_versions_match(self):
        plugin_version = next(
            ast.literal_eval(decorator.args[3])
            for decorator in self.plugin_class.decorator_list
            if decorator_name(decorator) == "register"
        )
        metadata = METADATA_PATH.read_text(encoding="utf-8")
        metadata_version = re.search(r"^version:\s*v?(.+?)\s*$", metadata, re.MULTILINE)

        self.assertIsNotNone(metadata_version)
        self.assertEqual(plugin_version, metadata_version.group(1))

    def test_list_mihome_devices_tool_exists_with_args_doc(self):
        """新增的 list_mihome_devices 工具必须存在且带 Args 文档"""
        tool_methods = [
            node for node in self.plugin_class.body
            if isinstance(node, ast.AsyncFunctionDef)
            and any(
                decorator_name(d) == "filter.llm_tool"
                and d.args
                and ast.literal_eval(d.args[0]) == "list_mihome_devices"
                for d in node.decorator_list
            )
        ]
        self.assertEqual(len(tool_methods), 1, "list_mihome_devices 工具必须存在且唯一")
        docstring = ast.get_docstring(tool_methods[0]) or ""
        self.assertIn("Args:", docstring, "list_mihome_devices 必须包含 Args 文档段")


if __name__ == "__main__":
    unittest.main()
