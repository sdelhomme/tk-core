"""
Copyright (c) 2012 Shotgun Software, Inc
"""
import os
import unittest2 as unittest

from mock import Mock, patch

import tank
from tank.api import Tank
from tank.errors import TankError
from tank.template import TemplatePath, TemplateString
from tank.templatekey import StringKey, IntegerKey, SequenceKey

from tank_test.tank_test_base import *

class TestInit(TankTestBase):
        
    def test_project_from_param(self):
        tank = Tank(self.project_root)
        self.assertEquals(self.project_root, tank.project_path)


class TestTemplateFromPath(TankTestBase):
    """Cases testing Tank.template_from_path method"""
    def setUp(self):
        super(TestTemplateFromPath, self).setUp()
        self.setup_fixtures()
        self.tk = Tank(self.project_root)

    def test_defined_path(self):
        """Resolve a path which maps to a template in the standard config"""
        file_path = os.path.join(self.project_root,
                'sequences/Sequence_1/shot_010/Anm/publish/shot_010.jfk.v001.ma')
        template = self.tk.template_from_path(file_path)
        self.assertIsInstance(template, TemplatePath)

    def test_undefined_path(self):
        """Resolve a path which does not map to a template"""
        file_path = os.path.join(self.project_root,
                'sequences/Sequence 1/shot_010/Anm/publish/')
        template = self.tk.template_from_path(file_path)
        self.assertTrue(template is None)

    def test_template_string(self):
        """Resolve 'path' which is a resolved TemplateString."""
        # resolved version of 'nuke_publish_name'
        str_path = "Nuke Script Name, v02"
        template = self.tk.template_from_path(str_path)
        self.assertIsNotNone(template)
        self.assertIsInstance(template, TemplateString)


class TestTemplatesLoaded(TankTestBase):
    """Test case for the loading of templates from project level config."""
    def setUp(self):
        super(TestTemplatesLoaded, self).setUp()
        self.setup_multi_root_fixtures()
        # some template names we know exist in the standard template
        self.expected_names = ["maya_shot_work", "nuke_shot_work"]
        self.tk = Tank(self.project_root)

    def test_templates_loaded(self):
        actual_names = self.tk.templates.keys()
        for expected_name in self.expected_names:
            self.assertTrue(expected_name in actual_names)

    def test_get_template(self):
        for expected_name in self.expected_names:
            template = self.tk.templates.get(expected_name)
            self.assertTrue(isinstance(template, TemplatePath))

    def test_project_roots_set(self):
        """Test project root on templates with alternate and primary roots are set correctly."""

        primary_template = self.tk.templates["shot_project"]
        self.assertEquals(self.project_root, primary_template.root_path)

        alt_template = self.tk.templates["maya_shot_publish"]
        self.assertEquals(self.alt_root_1, alt_template.root_path)


class TestPathsFromTemplate(TankTestBase):
    """Tests for tank.paths_from_template using test data based on sg_standard setup."""
    def setUp(self):
        super(TestPathsFromTemplate, self).setUp()
        self.setup_fixtures()
        # create project data
        # two sequences
        seq1_path = os.path.join(self.project_root, "sequences/Seq_1")
        self.add_production_path(seq1_path,
                            {"type":"Sequence", "id":1, "name": "Seq_1"})
        seq2_path = os.path.join(self.project_root, "sequences/Seq_2")
        self.add_production_path(seq2_path,
                            {"type":"Sequence", "id":2, "name": "Seq_2"})
        # one shot
        shot_path = os.path.join(seq1_path, "Shot_1")
        self.add_production_path(shot_path,
                            {"type":"Shot", "id":1, "name": "shot_1"})
        # one step
        step_path = os.path.join(shot_path, "step_name")
        self.add_production_path(step_path,
                            {"type":"Step", "id":1, "name": "step_name"})

        self.tk = Tank(self.project_root)

        # using template from standard setup
        self.template = self.tk.templates.get("maya_shot_work")

        # make some fake files with different versions
        fields = {"Sequence":"Seq_1",
                  "Shot": "shot_1",
                  "Step": "step_name",
                  "name": "filename"}
        fields["version"] = 1
        file_path = self.template.apply_fields(fields)
        self.file_1 = file_path
        self.create_file(self.file_1)
        fields["version"] = 2
        file_path = self.template.apply_fields(fields)
        self.file_2 = file_path
        self.create_file(self.file_2)


    def test_skip_sequence(self):
        """
        Test skipping the template key 'Sequence', which is part of the path 
        definition, returns files from other sequences.
        """
        skip_keys = "Sequence"
        fields = {}
        fields["Shot"] = "shot_1"
        fields["Step"] = "step_name"
        fields["version"] = 1
        fields["Sequence"] = "Seq_2"
        expected = [self.file_1]
        actual = self.tk.paths_from_template(self.template, fields, skip_keys=skip_keys)
        self.assertEquals(expected, actual)

    def test_skip_version(self):
        """
        Test skipping a template key which is part of the file definition returns
        multiple files.
        """
        skip_keys = "version"
        fields = {}
        fields["Shot"] = "shot_1"
        fields["Step"] = "step_name"
        fields["version"] = 3
        fields["Sequence"] = "Seq_1"
        expected = [self.file_1, self.file_2]
        actual = self.tk.paths_from_template(self.template, fields, skip_keys=skip_keys)
        self.assertEquals(set(expected), set(actual))

    def test_skip_invalid(self):
        """Test that files not valid for an template are not returned.

        This refers to bug reported in Ticket #17090
        """
        keys = {"Shot": StringKey("Shot"),
                "Sequence": StringKey("Sequence"),
                "Step": StringKey("Step"),
                "name": StringKey("name"),
                "version": IntegerKey("version", format_spec="03")}
        
        definition = "sequences/{Sequence}/{Shot}/{Step}/work/{name}.v{version}.nk"
        template = TemplatePath(definition, keys, self.project_root, "my_template")
        tk = tank.Tank(self.project_root)
        tk._templates = {template.name: template}
        bad_file_path = os.path.join(self.project_root, "sequences", "Sequence1", "Shot1", "Foot", "work", "name1.va.nk")
        good_file_path = os.path.join(self.project_root, "sequences", "Sequence1", "Shot1", "Foot", "work", "name.v001.nk")
        self.create_file(bad_file_path)
        self.create_file(good_file_path)
        ctx_fields = {"Sequence": "Sequence1", "Shot": "Shot1", "Step": "Foot"}
        result = tk.paths_from_template(template, ctx_fields)
        self.assertIn(good_file_path, result)
        self.assertNotIn(bad_file_path, result)


class TestPathsFromTemplateGlob(TankTestBase):
    """Tests for Tank.paths_from_template method which check the string sent to glob.glob."""
    def setUp(self):
        super(TestPathsFromTemplateGlob, self).setUp()
        self.tk = Tank(self.project_root)
        keys = {"Shot": StringKey("Shot"),
                "version": IntegerKey("version", format_spec="03"),
                "seq_num": SequenceKey("seq_num", format_spec="05")}

        self.template = TemplatePath("{Shot}/{version}/filename.{seq_num}", keys, root_path=self.project_root)

    @patch("tank.api.glob.iglob")
    def assert_glob(self, fields, expected_glob, skip_keys, mock_glob):
        # want to ensure that value returned from glob is returned
        expected = [os.path.join(self.project_root, "shot_1","001","filename.00001")]
        mock_glob.return_value = expected
        retval = self.tk.paths_from_template(self.template, fields, skip_keys=skip_keys)
        self.assertEquals(expected, retval)
        # Check glob string
        expected_glob = os.path.join(self.project_root, expected_glob)
        glob_actual = [x[0][0] for x in mock_glob.call_args_list][0]
        self.assertEquals(expected_glob, glob_actual)

    def test_fully_qualified(self):
        """Test case where all field values are supplied."""
        skip_keys = None
        fields = {}
        fields["Shot"] = "shot_name"
        fields["version"] = 4
        fields["seq_num"] = 45
        expected_glob = os.path.join("%(Shot)s", "%(version)03d", "filename.%(seq_num)05d") % fields 
        self.assert_glob(fields, expected_glob, skip_keys)

    def test_skip_dirs(self):
        """Test matching skipping at the directory level."""
        skip_keys = ["version"]
        fields = {}
        fields["Shot"] = "shot_name"
        fields["version"] = 4
        fields["seq_num"] = 45
        sep = os.path.sep
        glob_str = "%(Shot)s" + sep + "*" + sep + "filename.%(seq_num)05i"
        expected_glob =  glob_str % fields
        self.assert_glob(fields, expected_glob, skip_keys)

    def test_skip_file_token(self):
        """Test matching skipping tokens in file name."""
        skip_keys = ["seq_num"]
        fields = {}
        fields["Shot"] = "shot_name"
        fields["version"] = 4
        fields["seq_num"] = 45
        sep = os.path.sep
        glob_str = "%(Shot)s" + sep + "%(version)03d" + sep + "filename.*"
        expected_glob =  glob_str % fields
        self.assert_glob(fields, expected_glob, skip_keys)

    def test_missing_values(self):
        """Test skipping fields rather than using skip_keys."""
        skip_keys = None
        fields = {}
        fields["Shot"] = "shot_name"
        fields["seq_num"] = 45
        sep = os.path.sep
        glob_str = "%(Shot)s" + sep + "*" + sep + "filename.%(seq_num)05i" 
        expected_glob =  glob_str % fields
        self.assert_glob(fields, expected_glob, skip_keys)

    
class TestAbstractPathFromTemplate(TankTestBase):
    def setUp(self):
        super(TestAbstractPathFromTemplate, self).setUp()
        self.setup_fixtures()
        # create project data
        seq_path = os.path.join(self.project_root, "sequences/Seq_1")
        self.add_production_path(seq_path,
                            {"type":"Sequence", "id":1, "name": "Seq_1"})
        # one shot
        shot_path = os.path.join(seq_path, "Shot_1")
        self.add_production_path(shot_path,
                            {"type":"Shot", "id":1, "name": "shot_1"})
        # one step
        step_path = os.path.join(shot_path, "step_name")
        self.add_production_path(step_path,
                            {"type":"Step", "id":1, "name": "step_name"})

        self.tk = Tank(self.project_root)

        keys = {"Sequence": StringKey("Sequence"),
                "Shot": StringKey("Shot"),
                "Step": StringKey("Step"),
                "eye": StringKey("eye", default="%V", choices=["%V", "L", "R"]),
                "version": IntegerKey("version"),
                "frame": SequenceKey("frame", format_spec="03")}

        # create template with abstract and non-abstract keys
        definition = "sequences/{Sequence}/{Shot}/{Step}/images/{eye}/{Shot}.{version}.{frame}.ext"
        self.template = TemplatePath(definition, keys, self.project_root)

        # make some fake files with different frames
        self.fields = {"Sequence":"Seq_1",
                  "Shot": "shot_1",
                  "Step": "step_name",
                  "version": 13,
                  "eye": "L"}
        self.fields["frame"] = 1
        file_path = self.template.apply_fields(self.fields)
        self.file_1 = file_path
        self.create_file(self.file_1)

        self.fields["frame"] = 2
        file_path = self.template.apply_fields(self.fields)
        self.file_2 = file_path
        self.create_file(self.file_2)

        # change eye directory
        self.fields["eye"] = "R"
        file_path = self.template.apply_fields(self.fields)
        self.file_3 = file_path
        self.create_file(self.file_3)

    
    def test_all_abstract(self):
        # fields missing will be treated as abstract
        del(self.fields["eye"])
        del(self.fields["frame"])
        relative_path = os.path.join("sequences",
                                     "Seq_1",
                                     "shot_1",
                                     "step_name",
                                     "images",
                                     "%V",
                                     "shot_1.13.%03d.ext")
        expected = os.path.join(self.project_root, relative_path)
        result = self.tk.abstract_path_from_template(self.template, self.fields)
        self.assertEquals(expected, result)


    def test_frames_only(self):
        del(self.fields["frame"])
        self.fields["eye"] = "R"
        relative_path = os.path.join("sequences",
                                     "Seq_1",
                                     "shot_1",
                                     "step_name",
                                     "images",
                                     "R",
                                     "shot_1.13.%03d.ext")
        expected = os.path.join(self.project_root, relative_path)
        result = self.tk.abstract_path_from_template(self.template, self.fields)
        self.assertEquals(expected, result)

    def test_format_frames(self):
        self.fields["frame"] = "FORMAT:#d"
        self.fields["eye"] = "R"
        relative_path = os.path.join("sequences",
                                     "Seq_1",
                                     "shot_1",
                                     "step_name",
                                     "images",
                                     "R",
                                     "shot_1.13.###.ext")
        expected = os.path.join(self.project_root, relative_path)
        result = self.tk.abstract_path_from_template(self.template, self.fields)
        self.assertEquals(expected, result)

    def test_no_abstract(self):
        self.fields["frame"] = 2
        self.fields["eye"] = "R"
        relative_path = os.path.join("sequences",
                                     "Seq_1",
                                     "shot_1",
                                     "step_name",
                                     "images",
                                     "R",
                                     "shot_1.13.002.ext")
        expected = os.path.join(self.project_root, relative_path)
        result = self.tk.abstract_path_from_template(self.template, self.fields)
        self.assertEquals(expected, result)

    def test_no_files_on_disk(self):
        os.remove(self.file_1)
        os.remove(self.file_2)
        os.remove(self.file_3)
        result = self.tk.abstract_path_from_template(self.template, self.fields)
        self.assertIsNone(result)

    
class TestVersionProperty(TankTestBase):
    """
    test api.version property
    """
    def setUp(self):
        super(TestVersionProperty, self).setUp()
        self.tk = Tank(self.project_root)

    def test_version_property(self):
        self.assertEquals(self.tk.version, "HEAD")

class TestDocumentationProperty(TankTestBase):
    """
    test api.documentation_url property
    """
    def setUp(self):
        super(TestDocumentationProperty, self).setUp()
        self.tk = Tank(self.project_root)

    def test_doc_property(self):
        self.assertEquals(self.tk.documentation_url, None)

class TestTankFromPath(TankTestBase):
    def setUp(self):
        super(TestTankFromPath, self).setUp()
        self.setup_multi_root_fixtures()

    def test_primary_branch(self):
        """
        Test path from primary branch.
        """
        child_path = os.path.join(self.project_root, "child_dir")
        result = tank.tank_from_path(child_path)
        self.assertIsInstance(result, Tank)
        self.assertEquals(result.project_path, self.project_root)

    def test_alternate_branch(self):
        """
        Test path not from primary branch.
        """
        child_path = os.path.join(self.alt_root_1, "child_dir")
        result = tank.tank_from_path(child_path)
        self.assertIsInstance(result, Tank)
        self.assertEquals(result.project_path, self.project_root)

    def test_bad_path(self):
        """
        Test path not in project tree.
        """
        bad_path = os.path.dirname(self.tank_temp)
        self.assertRaises(TankError, tank.tank_from_path, bad_path)

    def test_tank_temp(self):
        """
        Test passing in studio path.
        """
        self.assertRaises(TankError, tank.tank_from_path, self.tank_temp)

class Test_GetHookPath(TankTestBase):
    def setUp(self):
        super(Test_GetHookPath, self).setUp()
        self.setup_fixtures()

    def test_core_path(self):
        """
        Case that core hook does not exist at project level, find it in the core area.
        """
        # Check for one of the core hooks
        # Path will be where code is stored...
        expected = os.path.join( "core", "hooks", "create_folder.py")
        result = tank.api._get_hook_path("create_folder", self.project_root)
        self.assertTrue(result.endswith(expected))
        self.assertFalse(result.startswith(self.project_root))

    def test_project_override(self):
        """
        Case that project core hooks area contains a hook with matching name.
        """
        # Check for hook overrriden in the project core hooks area
        expected = os.path.join(self.project_root, "tank", "config", "core", "hooks", "context_additional_entities.py")
        self.assertEquals(expected, tank.api._get_hook_path("context_additional_entities", self.project_root))


if __name__ == "__main__":
    unittest.main()
