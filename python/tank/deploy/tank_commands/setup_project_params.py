# Copyright (c) 2013 Shotgun Software Inc.
# 
# CONFIDENTIAL AND PROPRIETARY
# 
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit 
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your 
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights 
# not expressly granted therein are reserved by Shotgun Software Inc.

import sys
import os
import re
import tempfile
import uuid

from ...platform import constants
from ...util import shotgun
from ... import hook
from ...errors import TankError
from ... import pipelineconfig

from ..zipfilehelper import unzip_file
from .. import util as deploy_util

from .setup_project_core import _copy_folder

from tank_vendor import yaml


class ProjectSetupParameters(object):
    """
    Class that holds all the various parameters needed to run a project setup.
    
    This class allows for various forms of validation and inspection of all the data required 
    to set up a project.
    
    Parameters are typically set in this order:
    
    - get some information about the configuration 
    - set the template configuration you want to use - set_config_uri()
    
    - set the project id - set_project_id()
    - get a suggested project name - get_default_project_disk_name()
    - set project disk name - set_project_disk_name() (can validate on beforehand using validate_project_disk_name())
    
    - get a suggested configuration location - get_default_configuration_location()
    - set the configuration location - set_configuration_location()
    
    - validate using validate_project_io and validate_config_io
    
    - run project setup!
    
    """
    
    def __init__(self, log, sg, sg_app_store, sg_app_store_script_user):
        """
        Constructor
        
        :param log: python logger
        :param sg: shotgun connection
        :param sg_app_store: shotgun app store connection
        :param sg_app_store_script_user: sg-style link dict representing the script user used to 
                                         connect to the app store
        """
        
        # set up handles
        self._sg = sg
        self._log = log
        self._sg_app_store = sg_app_store
        self._sg_app_store_script_user = sg_app_store_script_user
        
        # initialize data members - config
        self._cached_config_templates = {}
        self._config_template = None
        self._config_name = None
        self._config_description = None
        self._config_path = None
        
        # expert setting auto path mode
        self._auto_path = False
        
        # initialize data members - project
        self._project_id = None
        self._force_setup = None
        self._project_name = None
    
    
    ################################################################################################################
    # Configuration template related logic     
        
    
    def validate_config_uri(self, config_uri):
        """
        Validates a configuration template to check if it is compatible with the current Shotgun setup.
        This will download the config, validate it to ensure that it is compatible with the 
        constraints (versions of core and shotgun) of this system. 
        
        If locating, downloading, or validating the config fails, exceptions will be raised.
        
        Once the config exists and is compatible, the storage situation is reviewed against shotgun.
        A dictionary with a breakdown of all storages required by the configuration is returned:
        
        {
          "primary" : { "description": "Description",
                        "exists_on_disk": False,
                        "defined_in_shotgun": True,
                        "darwin": "/mnt/foo",
                        "win32": "z:\mnt\foo",
                        "linux2": "/mnt/foo"},
                                     
          "textures" : { "description": None,
                         "exists_on_disk": False,
                         "defined_in_shotgun": True,
                         "darwin": None,
                         "win32": "z:\mnt\foo",
                         "linux2": "/mnt/foo"}                                    
         }
        
        :param config_uri: Configuration uri representing the location of a config
        :returns: dictionary with storage data, see above.
        """
        
        # see if we got it cached
        if config_uri not in self._cached_config_templates:
            # first download, read and parse the configuration template
            # this call may mean downloading stuff from the internet.
            config_template = TemplateConfiguration(config_uri, 
                                                    self._sg, 
                                                    self._sg_app_store, 
                                                    self._sg_app_store_script_user, 
                                                    self._log)
            self._cached_config_templates[config_uri] = config_template

        return self._cached_config_templates[config_uri].resolve_storages()

        
        
    def set_config_uri(self, config_uri, check_storage_path=True):
        """
        Sets the configuration uri to use for this project.
        As part of this command, a template configuration may be downloaded over the network,
        either from git or from the toolkit app store.
        Raises exceptions in case the configuration is not compatible with the current shotgun setup.
        
        :param config_uri: Configuration uri representing the location of a config
        :param check_storage_path: Validate that storage paths exists on disk
        """
        
        # cache, get storage breakdown and run basic validation
        storage_data = self.validate_config_uri(config_uri) 
        
        # now validate storages
        #        
        # {
        #   "primary" : { "description": "Description",
        #                 "exists_on_disk": False,
        #                 "defined_in_shotgun": True,
        #                 "darwin": "/mnt/foo",
        #                 "win32": "z:\mnt\foo",
        #                 "linux2": "/mnt/foo"},
        #                              
        #   "textures" : { "description": None,
        #                  "exists_on_disk": False,
        #                  "defined_in_shotgun": True,
        #                  "darwin": None,
        #                  "win32": "z:\mnt\foo",
        #                  "linux2": "/mnt/foo"}                                    
        #  }
                
        for storage_name in storage_data:
            
            if not storage_data[storage_name]["defined_in_shotgun"]:
                raise TankError("The storage '%s' required by the configuration has not been defined in Shotgun. "
                                "In order to fix this, please navigate to the Site Preferences in Shotgun "
                                "and set up a new local file storage." % storage_name)

            elif storage_data[storage_name][sys.platform] is None:
                raise TankError("The Shotgun Local File Storage '%s' does not have a path defined "
                                "for the current operating system!" % storage_name)
            
            elif check_storage_path and storage_data[storage_name]["exists_on_disk"]:
                local_path = storage_data[storage_name][sys.platform]
                raise TankError("The path on disk '%s' defined in the Shotgun Local File Storage '%s' does "
                                "not exist!" % (local_path, storage_name))                            
        
        # all checks passed! Populate official variables
        # note that the validate_config_uri method cached the config for us
        # so can just assign the object from the cache.
        self._config_template = self._cached_config_templates[config_uri]
        self._storage_data = storage_data
        
    def get_configuration_display_name(self):
        """
        Returns the display name of the configuration template
        
        :returns: Configuration display name string, none if not defined
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")
        
        return self._config_template.get_name() 
        
    def get_configuration_description(self):
        """
        Returns the description of the associated configuration template
        
        :returns: Configuration description string, None if not defined
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")
        
        return self._config_template.get_description()
    
    def get_required_storages(self):
        """
        Returns a list of storage names which are required for this project.
        
        :returns: list of strings
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")
        
        return self._storage_data.keys()

    def get_storage_description(self, storage_name):
        """
        Returns the description of a storage required by a configuration
        
        :param storage_name: Storage name
        :returns: Storage description string, None if not defined
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")
        
        if storage_name not in self._storage_data:
            raise TankError("Configuration template does not contain a storage with name '%s'!" % storage_name) 
        
        return self._storage_data.get(storage_name).get("description")
        
    def get_storage_path(self, storage_name, platform):
        """    
        Returns the storage root path given a platform and a storage, as defined in Shotgun
        Note that this path has not been cleaned up, and may for example contain slashes at the end.
        
        :param storage_name: Storage name
        :param platform: operating system, sys.platform syntax 
        :returns: path
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")
        
        if storage_name not in self._storage_data:
            raise TankError("Configuration template does not contain a storage with name '%s'!" % storage_name) 
        
        return self._storage_data.get(storage_name).get(platform)

    def create_configuration(self, target_path):
        """
        Sets up the associated template configuration. Copies files.
        
        :param target_path: Location where the config should be set up. 
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")
        
        return self._config_template.create_configuration(target_path)
        
    ################################################################################################################
    # Project related logic     
        
    def set_project_id(self, project_id, force=False):
        """
        Sets the project id and validates that this id is valid.
        
        :param project_id: Shotgun project id
        :param force: If true, existing projects can be overwritten
        """
        proj = self._sg.find_one("Project", [["id", "is", project_id]], ["name", "tank_name"])
    
        if proj is None:
            raise TankError("Could not find a project with id %s!" % self._project_id)

        # if force is false then tank_name must be empty
        if force == False and proj["tank_name"] is not None:
            raise TankError("You are trying to set up a project which has already been set up. If you want to do "
                            "this, make sure to set the force parameter.")

        self._project_id = project_id
        self._force_setup = force
         
    def get_default_project_disk_name(self):
        """
        Returns the default folder name for a project
        
        :returns: project name string. This may contain slashes if the project name spans across
                  more than one folder.
        """
        
        if self._project_id is None:
            raise TankError("No project id specified!")
        
        # see if there is a hook to procedurally evaluate this
        project_name_hook = shotgun.get_project_name_studio_hook_location()
        if os.path.exists(project_name_hook):
            # custom hook is available!
            suggested_folder_name = hook.execute_hook(project_name_hook, 
                                                      parent=None, 
                                                      sg=self._sg, 
                                                      project_id=self._project_id)
        else:
            # construct a valid name - replace white space with underscore and lower case it.
            proj = self._sg.find_one("Project", [["id", "is", self._project_id]], ["name"])
            suggested_folder_name = re.sub("\W", "_", proj.get("name")).lower()
        
        return suggested_folder_name
        
    def validate_project_disk_name(self, project_name):
        """
        Validates that the given project disk name is valid.
        Raises exceptions in case the name is not valid.
        
        :param project_name: project disk name
        """
        if project_name.startswith("/"):
            raise TankError("A project disk name cannot start with a slash!")

        if project_name.endswith("/"):
            raise TankError("A project disk name cannot end with a slash!")
        
        # basic validation of folder name
        # note that the value can contain slashes and span across multiple folders
        if re.match("^[\./a-zA-Z0-9_-]+$", project_name) is None:
            raise TankError("Invalid project folder '%s'! Please use alphanumerics, "
                            "underscores and dashes." % project_name)
        
    def preview_project_path(self, storage_name, project_name, platform):
        """
        Returns a full project path for a given storage. Returns None if the project name is not valid.
        A configuration template must have been specified prior to executing this command.
        The path returned may not exist on disk but never ends with a path separator.
        
        :param storage_name: Name of storage for which to preview the project path
        :param project_name: Project disk name to preview
        :param platform: Os platform as a string, sys.platform style (e.g. linux2/win32/darwin)
        
        :returns: full path
        """
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")

        # basic validation of project name
        try:
            self.validate_project_disk_name(project_name)
        except TankError:
            # validation failed!
            return None
        
        # get the storage path
        storage_path = self.get_storage_path(storage_name, platform)
        
        if storage_path is None:
            return None
        
        # get rid of any trailing slashes
        storage_path = storage_path.rstrip("/\\")
        # append the project name
        storage_path += "/%s" % project_name
        # note that project name can be 'foo/bar' with a forward slash for all platforms
        if platform == "win32":
            # ensure back slashes all the way
            storage_path = storage_path.replace("/", "\\")
        else:
            # ensure slashes all the way
            storage_path = storage_path.replace("\\", "/")
        
        return storage_path

    def set_project_disk_name(self, project_name):
        """
        Sets a project disk name to use for this configuration.
        May raise exception if the name is not valid.
        
        :param project_name: name of project
        """
        self.validate_project_disk_name(project_name)
        self._project_name = project_name
    
    def get_project_id(self):
        """
        Returns the project id for the project to be set up.
        
        :returns: Shotgun project id as int
        """
        if self._project_id is None:
            raise TankError("No project id specified!")
        
        return self._project_id
        
    def get_force_setup(self):
        """
        Should setup be forced?
        
        :returns: a boolean flag indicating whether the setup should be forced or not.
        """
        if self._project_id is None:
            raise TankError("No project id specified!")
        
        return self._force_setup
        
    def get_project_disk_name(self):
        """
        Returns the disk name to be given to the project. This may be a simple name 
        "test_project" or may contain slashes "test/project" for project names
        which span across multiple folder levels, however it never starts or ends
        with a slash.
        
        :returns: project name as a string
        """
        if self._project_name is None:
            raise TankError("No project name specified!")

        return self._project_name
    
    def get_project_path(self, storage_name, platform):
        """    
        Returns the project path given a platform and a storage. Can be None for undefined storages.
        The path returned may not exist on disk but never ends with a path separator.
        
        :param storage_name: Name of storage for which to preview the project path
        :param platform: Os platform as a string, sys.platform style (e.g. linux2/win32/darwin)
        
        :returns: full path
        """
        if self._project_name is None:
            raise TankError("No project name specified!")
        
        if self._config_template is None:
            raise TankError("Please specify a configuration template!")

        return self.preview_project_path(storage_name, self._project_name, platform)
            
    
    ################################################################################################################
    # Configuration template related logic     
    
    def set_auto_path(self, status):
        """
        Defines if auto-path should be on or off.
        Auto-path means that the pipeline configuration entry in
        Shotgun does not actually encode the path to where the configuration
        is located on disk - this is instead purely kept on the disk side
        
        :param status: boolean indicating if auto path should be used
        """
        self._auto_path = status
        
    def is_auto_path(self):
        """
        Returns the auto-path status. See set_auto_path for details.
        
        :returns: boolean indicating if auto path should be used
        """
        return self._auto_path
    
    def get_default_configuration_location(self):
        """
        Returns default suggested location for configurations.
        Returns a dictionary with sys.platform style keys linux2/win32/darwin, e.g.
        
        { "darwin": "/foo/bar/project_name", 
          "linux2": "/foo/bar/project_name",
          "win32" : "c:\foo\bar\project_name"}        

        :returns: dictionary with paths
        """

        if self._project_name is None:
            raise TankError("Must specify a project name before accessing config locaton defaults!")    
    
        # figure out the config install location. There are three cases to deal with
        # - 0.13 style layout, where they all sit together in an install location
        # - 0.12 style layout, where there is a tank folder which is the studio location
        #   and each project has its own folder.
        # - something else!
                
        location = {"darwin": None, "linux2": None, "win32": None}
        
        # get the path to the primary storage  
        primary_local_path = self.get_storage_path(constants.PRIMARY_STORAGE_NAME, sys.platform)        
        
        core_locations = self._get_current_core_install_location_data()
        
        if os.path.abspath(os.path.join(core_locations[sys.platform], "..")).lower() == primary_local_path.lower():
            # ok the parent of the install root matches the primary storage - means OLD STYLE (pre core 0.12)
            #
            # in this setup, we would have the following structure: 
            # /studio              <--- primary storage
            # /studio/tank         <--- core API install
            # /studio/project      <--- project data location
            # /studio/project/tank <--- toolkit configuation location

            if self.get_project_path(constants.PRIMARY_STORAGE_NAME, "darwin"):
                location["darwin"] = "%s/tank" % self.get_project_path(constants.PRIMARY_STORAGE_NAME, "darwin") 
                                                     
            if self.get_project_path(constants.PRIMARY_STORAGE_NAME, "linux2"):
                location["linux2"] = "%s/tank" % self.get_project_path(constants.PRIMARY_STORAGE_NAME, "linux2") 

            if self.get_project_path(constants.PRIMARY_STORAGE_NAME, "win32"):
                location["win32"] = "%s\\tank" % self.get_project_path(constants.PRIMARY_STORAGE_NAME, "win32") 

        else:
            # Core v0.12+ style setup - this is what is our default recommended setup
            # here, the project data is treated as a completely separate thing.
            #
            # typical new style setup (not showing project data locations)
            # /software/studio <-- core API install
            #
            # /software/proj_a  <-- project configuration
            # /software/proj_b  <-- project configuration
            # /software/proj_c  <-- project configuration
            #
            # In this case, we can determine the location of /software/studio by looking 
            # at the location of the running code.
            # we then suggest a configuration relative to this
            
            # get the project name on disk - note that this may contain slashes
            project_name_chunks = self.get_project_disk_name().split("/") # ['multi', 'tier', 'name']
            
            # note: linux_install_root.startswith("/") handles the case where the config file says "undefined"
            
            if core_locations["linux2"]:
                chunks = core_locations["linux2"].split("/") # e.g. /software/studio -> ['', 'software', 'studio']
                chunks.pop() # pop the studio bit (e.g ['', 'software'])
                chunks.extend(project_name_chunks) # append project name 
                location["linux2"] = "/".join(chunks)
            
            if core_locations["darwin"]:
                chunks = core_locations["darwin"].split("/") # e.g. /software/studio -> ['', 'software', 'studio']
                chunks.pop() # pop the studio bit (e.g ['', 'software'])
                chunks.extend(project_name_chunks) # append project name
                location["darwin"] = "/".join(chunks)
            
            if core_locations["win32"]:
                # split path into chunks
                # e.g. c:\software\studio -> ['c:', 'software', 'studio']
                # e.g. \\myserver\mymount\software\studio -> ['', '', 'myserver', 'mymount', 'software', 'studio']
                chunks = core_locations["win32"].split("\\") 
                chunks.pop() # pop the studio bit
                chunks.extend(project_name_chunks) # append project name
                location["win32"] = "\\".join(chunks)

        return location

    def set_configuration_location(self, linux_path, windows_path, macosx_path):
        """
        Sets the desired path to a pipeline configuration.
        Paths can be None, indicating that the path is not defined on a platform.
        
        :param linux_path: Path on linux 
        :param windows_path: Path on windows
        :param macosx_path: Path on mac
        """
        self._config_path = {}
        self._config_path["linux2"] = linux_path
        self._config_path["win32"] = windows_path
        self._config_path["darwin"] = macosx_path
        
    def get_configuration_location(self, platform):
        """    
        Returns the path to the configuration for a given platform.
        The path returned has not been validated and may not be correct nor exist.
        
        :param platform: Os platform as a string, sys.platform style (e.g. linux2/win32/darwin)
        :returns: path to pipeline configuration.
        """
        if self._config_path is None:
            raise TankError("No configuration location has been set!")
        
        return self._config_path[platform]


    ################################################################################################################
    # Accessing which core API to use

    def get_associated_core_path(self, platform):
        """
        Return the location of the currently running API, given an os platform.
        Note that values returned can be none in case the core API location
        has not been defined on a platform.

        :param platform: Os platform as a string, sys.platform style (e.g. linux2/win32/darwin)
        :returns: path to pipeline configuration.
        """
        core_paths = self._get_current_core_install_location_data()        
        return core_paths[platform]

    def _get_current_core_install_location_data(self):
        """
        Given the location of the running code, find the configuration which holds
        the installation location on all platforms. Return the content of this file.
        Note that some entries may be None in case a core wasn't defined for that platform.
        
        :returns: dict with keys linux2, darwin and win32
        """
    
        core_api_root = os.path.abspath(os.path.join( os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))
        core_cfg = os.path.join(core_api_root, "config", "core")
    
        if not os.path.exists(core_cfg):
            full_path_to_file = os.path.abspath(os.path.dirname(__file__))
            raise TankError("Cannot resolve the core configuration from the location of the Toolkit Code! "
                            "This can happen if you try to move or symlink the Toolkit API. The "
                            "Toolkit API is currently picked up from %s which is an "
                            "invalid location." % full_path_to_file)
        
        location_file = os.path.join(core_cfg, "install_location.yml")
        if not os.path.exists(location_file):
            raise TankError("Cannot find '%s' - please contact support!" % location_file)
    
        # load the config file
        try:
            open_file = open(location_file)
            try:
                location_data = yaml.load(open_file)
            finally:
                open_file.close()
        except Exception, error:
            raise TankError("Cannot load config file '%s'. Error: %s" % (location_file, error))

        # do some cleanup on this file - sometimes there are entries that say "undefined"
        # turn those into null values
        linux_path = location_data.get("Linux")
        macosx_path = location_data.get("Darwin")
        win_path = location_data.get("Windows")
        
        if linux_path is not None and not linux_path.startswith("/"):
            linux_path = None
        if macosx_path is not None and not macosx_path.startswith("/"):
            macosx_path = None
        if win_path is not None and not (win_path.startswith("\\") or win_path[1] == ":"):
            win_path = None

        # return data using sys.platform jargon
        return {"win32": win_path, "darwin": macosx_path, "linux2": linux_path } 


    ################################################################################################################
    # General validation     
    
    def validate_project_io(self):
        """
        Performs basic I/O checks to ensure that the tank folder can be written to each project location.
        (note: this will change as part of the 0.15 changes we are making)
        """    
        
        # get the location of the configuration
        config_path_current_os = self.get_configuration_location(sys.platform) 
        
        # validate the local storages
        for storage_name in self.get_required_storages():
            
            # get the project path for this storage
            # note! at this point, the storage root has been checked and exists on disk.
            project_path_local_os = self.get_project_path(storage_name, sys.platform)
                            
            # make sure that the storage location is not the same folder
            # as the pipeline config location. That will confuse tank.
            if config_path_current_os == project_path_local_os:
                raise TankError("Your configuration location '%s' has been set to the same "
                                "as one of the storage locations. This is not supported!" % config_path_current_os)
            
            if not os.path.exists(project_path_local_os):
                raise TankError("The Project path %s for storage %s does not exist on disk! "
                                "Please create it and try again!" % (project_path_local_os, storage_name))
        
            tank_folder = os.path.join(project_path_local_os, "tank")
            if os.path.exists(tank_folder):
                # tank folder exists - make sure it is writable
                if not os.access(tank_folder, os.W_OK|os.R_OK|os.X_OK):
                    raise TankError("The permissions setting for '%s' is too strict. The current user "
                                    "cannot create files or folders in this location." % tank_folder)
            else:
                # no tank folder has been created in this storage
                # make sure we can create it
                if not os.access(project_path_local_os, os.W_OK|os.R_OK|os.X_OK):
                    raise TankError("The permissions setting for '%s' is too strict. The current user "
                                    "cannot create a tank folder in this location." % project_path_local_os)
                
    def validate_config_io(self):
        """
        Performs basic I/O checks to ensure that the config can be created in the specified location
        """    
        
        # get the location of the configuration
        config_path_current_os = self.get_configuration_location(sys.platform) 
        
        # validate that the config location is not taken
        if os.path.exists(config_path_current_os):
            # pc location already exists - make sure it doesn't already contain an install
            if os.path.exists(os.path.join(config_path_current_os, "install")) or \
               os.path.exists(os.path.join(config_path_current_os, "config")):
                raise TankError("Looks like the location '%s' already contains a "
                                "configuration!" % config_path_current_os)
            # also make sure it has right permissions
            if not os.access(config_path_current_os, os.W_OK|os.R_OK|os.X_OK):
                raise TankError("The permissions setting for '%s' is too strict. The current user "
                                "cannot create files or folders in this location." % config_path_current_os)
            
        else:
            # path does not exist! 
            # make sure parent path exists and is writable
            # find an existing parent path
            parent_config_path_current_os = os.path.dirname(config_path_current_os)
        
            if not os.path.exists(parent_config_path_current_os):
                raise TankError("The folder '%s' does not exist! Please create "
                                "it before proceeding!" % parent_config_path_current_os)
                    
            # and make sure we can create a folder in it
            if not os.access(parent_config_path_current_os, os.W_OK|os.R_OK|os.X_OK):
                raise TankError("Cannot create a project configuration in location '%s'! "
                                "The permissions setting for the parent folder '%s' "
                                "is too strict. The current user "
                                "cannot create folders in this location. Please create the "
                                "project configuration folder by hand and then re-run the project "
                                "setup." % (config_path_current_os, parent_config_path_current_os))
        
    





class TemplateConfiguration(object):
    """
    Functionality for handling installation and validation of tank configs.
    This class abstracts download and resolve of various config URLs, such as 
    
    - app store based configs
    - git based configs
    - file system configs
    - configs copied across from other projects
    
    The constructor is initialized with a config_uri which can have the following syntax:
    
    - toolkit app store syntax:    tk-config-default
    - git syntax (ends with .git): git@github.com:shotgunsoftware/tk-config-default.git
                                   https://github.com/shotgunsoftware/tk-config-default.git
                                   /path/to/bare/repo.git
    - file system location:        /path/to/config
    
    For the app store, the config is downloaded and unpacked into the project location
    For the file system uri, the config folder is copied into the project location
    For git, the git repo is cloned into the config location, therefore being a live repository
    to which changes later on can be pushed or pulled.
    """
    
    def __init__(self, config_uri, sg, sg_app_store, script_user, log):
        """
        Constructor
        
        :param config_uri: location of config (see constructor docs for details)
        :param sg: Shotgun site API instance
        :param sg_app_store: Shotgun app store API instance
        :param script_user: The app store script entity used to connect. Dictionary with type and id.
        :param log: Log channel 
        """
        self._sg = sg
        self._sg_app_store = sg_app_store
        self._script_user = script_user
        self._log = log
        
        # now extract the cfg and validate
        old_umask = os.umask(0)
        try:
            (self._cfg_folder, self._config_mode) = self._process_config(config_uri)
        finally:
            os.umask(old_umask)
        self._config_uri = config_uri
        self._roots_data = self._read_roots_file()

        # if there are more than zero storages defined, ensure one of them is the primary storage
        if len(self._roots_data) > 0 and constants.PRIMARY_STORAGE_NAME not in self._roots_data:
            raise TankError("Looks like your configuration does not have a primary storage. "
                            "This is required. Please contact support for more info.")

        # validate that we are running recent enough versions of core and shotgun
        info_yml = os.path.join(self._cfg_folder, constants.BUNDLE_METADATA_FILE)
        if not os.path.exists(info_yml):
            self._manifest = {}
            self._log.warning("Could not find manifest file %s. "
                              "Project setup will proceed without validation." % info_yml)
        else:
            # check manifest
            try:
                file_data = open(info_yml)
                try:
                    self._manifest = yaml.load(file_data)
                finally:
                    file_data.close()
            except Exception, e:
                raise TankError("Cannot load configuration manifest '%s'. Error: %s" % (info_yml, e))
    
            # perform checks
            if "requires_shotgun_version" in self._manifest:
                # there is a sg min version required - make sure we have that!
                
                required_version = self._manifest["requires_shotgun_version"]
        
                # get the version for the current sg site as a string (1.2.3)
                sg_version_str = ".".join([ str(x) for x in self._sg.server_info["version"]])
        
                if deploy_util.is_version_newer(required_version, sg_version_str):
                    raise TankError("This configuration requires Shotgun version %s "
                                    "but you are running version %s" % (required_version, sg_version_str))
                else:
                    self._log.debug("Config requires shotgun %s. "
                                    "You are running %s which is fine." % (required_version, sg_version_str))
                        
            if "requires_core_version" in self._manifest:
                # there is a core min version required - make sure we have that!
                
                required_version = self._manifest["requires_core_version"]
                
                # now figure out the current version of the currently running core API
                # and compare against that
                curr_core_version = pipelineconfig.get_core_api_version_based_on_current_code()
        
                if deploy_util.is_version_newer(required_version, curr_core_version):        
                    raise TankError("This configuration requires Toolkit Core version %s "
                                    "but you are running version %s" % (required_version, curr_core_version))
                else:
                    self._log.debug("Config requires Toolkit Core %s. "
                                    "You are running %s which is fine." % (required_version, curr_core_version))
    

    ################################################################################################
    # Helper methods

    def _read_roots_file(self):
        """
        Read, validate and return the roots data from the config.
        Example return data structure:
        
        { "primary": {"description":  "desc",
                      "mac_path":     "/asd",
                      "linux_path":   None,
                      "windows_path": "/asd" }
        }
        
        :returns: A dictionary for keyed by storage
        """
        # get the roots definition
        root_file_path = os.path.join(self._cfg_folder, "core", "roots.yml")
        if os.path.exists(root_file_path):
            root_file = open(root_file_path, "r")
            try:
                # if file is empty, initializae with empty dict...
                roots_data = yaml.load(root_file) or {}
            finally:
                root_file.close()
            
            # validate it
            for x in roots_data:
                if "mac_path" not in roots_data[x]:
                    roots_data[x]["mac_path"] = None
                if "linux_path" not in roots_data[x]:
                    roots_data[x]["linux_path"] = None
                if "windows_path" not in roots_data[x]:
                    roots_data[x]["windows_path"] = None
            
        else: 
            # set up default roots data
            roots_data = { constants.PRIMARY_STORAGE_NAME: 
                            { "description": "A location where the primary data is located.",
                              "mac_path": "/studio/projects", 
                              "linux_path": "/studio/projects", 
                              "windows_path": "\\\\network\\projects"
                            },                          
                          }
            
        return roots_data
        
    def _process_config_zip(self, zip_path):
        """
        unpacks a zip config into a temp location.
        
        :param zip_path: path to zip file to unpack
        :returns: tmp location on disk where config now resides
        """
        # unzip into temp location
        self._log.debug("Unzipping configuration and inspecting it...")
        zip_unpack_tmp = os.path.join(tempfile.gettempdir(), uuid.uuid4().hex)
        unzip_file(zip_path, zip_unpack_tmp)
        template_items = os.listdir(zip_unpack_tmp)
        for item in ["core", "env", "hooks"]:
            if item not in template_items:
                raise TankError("Config zip '%s' is missing a %s folder!" % (zip_path, item))
        self._log.debug("Configuration looks valid!")
        
        return zip_unpack_tmp
    
    def _process_config_app_store(self, config_name):
        """
        Downloads a config zip from the app store and unzips it.
        
        :param config_name: App store config bundle name
        :returns: tmp location on disk where config now resides 
        """
        
        if self._sg_app_store is None:
            raise TankError("Cannot download config - you are not connected to the app store!")
        
        # try download from app store...
        parent_entity = self._sg_app_store.find_one(constants.TANK_CONFIG_ENTITY, 
                                              [["sg_system_name", "is", config_name ]],
                                              ["code"]) 
        if parent_entity is None:
            raise Exception("Cannot find a config in the app store named %s!" % config_name)
        
        # get latest code
        latest_cfg = self._sg_app_store.find_one(constants.TANK_CONFIG_VERSION_ENTITY, 
                                           filters = [["sg_tank_config", "is", parent_entity],
                                                      ["sg_status_list", "is_not", "rev" ],
                                                      ["sg_status_list", "is_not", "bad" ]], 
                                           fields=["code", constants.TANK_CODE_PAYLOAD_FIELD],
                                           order=[{"field_name": "created_at", "direction": "desc"}])
        if latest_cfg is None:
            raise Exception("It looks like this configuration doesn't have any versions uploaded yet!")
        
        # now have to get the attachment id from the data we obtained. This is a bit hacky.
        # data example for the payload field, as returned by the query above:
        # {'url': 'http://tank.shotgunstudio.com/file_serve/attachment/21', 'name': 'tank_core.zip',
        #  'content_type': 'application/zip', 'link_type': 'upload'}
        #
        # grab the attachment id off the url field and pass that to the download_attachment()
        # method below.
        try:
            attachment_id = int(latest_cfg[constants.TANK_CODE_PAYLOAD_FIELD]["url"].split("/")[-1])
        except:
            raise TankError("Could not extract attachment id from data %s" % latest_cfg)
    
        self._log.debug("Downloading Config %s %s from the App Store..." % (config_name, latest_cfg["code"]))
        
        zip_tmp = os.path.join(tempfile.gettempdir(), "%s_tank_cfg.zip" % uuid.uuid4().hex)
    
        bundle_content = self._sg_app_store.download_attachment(attachment_id)
        fh = open(zip_tmp, "wb")
        fh.write(bundle_content)
        fh.close()
    
        # and write a custom event to the shotgun event log to indicate that a download
        # has happened.
        data = {}
        data["description"] = "Config %s %s was downloaded" % (config_name, latest_cfg["code"])
        data["event_type"] = "TankAppStore_Config_Download"
        data["entity"] = latest_cfg
        data["user"] = self._script_user
        data["project"] = constants.TANK_APP_STORE_DUMMY_PROJECT
        data["attribute_name"] = constants.TANK_CODE_PAYLOAD_FIELD
        self._sg_app_store.create("EventLogEntry", data)
    
        # got a zip! Pass to zip extractor...
        return self._process_config_zip(zip_tmp)
    
    def _process_config_dir(self, dir_path):
        """
        Validates that the directory contains a tank config
        """
        template_items = os.listdir(dir_path)
        for item in ["core", "env", "hooks"]:
            if item not in template_items:
                raise TankError("Config location '%s' missing a %s folder!" % (dir_path, item))
        self._log.debug("Configuration looks valid!")
        return dir_path
        
    def _process_config_git(self, git_repo_str):
        """
        Validate that a git repo is correct, download it to a temp location
        
        :param git_repo_str: Git repository string
        :returns: tmp location on disk where config now resides
        """
        
        self._log.debug("Attempting to clone git uri '%s' into a temp location "
                        "for introspection..." % git_repo_str)
        
        clone_tmp = os.path.join(tempfile.gettempdir(), uuid.uuid4().hex)
        self._log.info("Attempting to clone git repository '%s'..." % git_repo_str)
        self._clone_git_repo(git_repo_str, clone_tmp)
        
        return clone_tmp
        
    def _process_config(self, config_uri):
        """
        Looks at the starter config string and tries to convert it into a folder
        Returns a path to a config.
        
        :param config_uri: config path of some kind (git/appstore/local)
        :returns: tuple with (tmp_path_to_config, config_type) where config_type is local/git/app_store
        """
        # three cases:
        # tk-config-xyz
        # /path/to/file.zip
        # /path/to/folder
        if config_uri.endswith(".git"):
            # this is a git repository!
            self._log.info("Hang on, loading configuration from git...")
            return (self._process_config_git(config_uri), "git")
            
        elif os.path.sep in config_uri:
            # probably a file path!
            if os.path.exists(config_uri):
                # either a folder or zip file!
                if config_uri.endswith(".zip"):
                    self._log.info("Hang on, unzipping configuration...")
                    return (self._process_config_zip(config_uri), "local")
                else:
                    self._log.info("Hang on, loading configuration...")
                    return (self._process_config_dir(config_uri), "local")
            else:
                raise TankError("File path %s does not exist on disk!" % config_uri)    
        
        elif config_uri.startswith("tk-"):
            # app store!
            self._log.info("Hang on, loading configuration from the app store...")
            return (self._process_config_app_store(config_uri), "app_store")
        
        else:
            raise TankError("Don't know how to handle config '%s'" % config_uri)
        
    def _clone_git_repo(self, repo_path, target_path):
        """
        Clone the specified git repo into the target path
        
        :param repo_path:   The git repo path to clone
        :param target_path: The target path to clone the repo to
        :raises:            TankError if the clone command fails
        """
        # Note: git doesn't like paths in single quotes when running on 
        # windows - it also prefers to use forward slashes!
        sanitized_repo_path = repo_path.replace(os.path.sep, "/")
        if os.system("git clone \"%s\" \"%s\"" % (sanitized_repo_path, target_path)) != 0:
            raise TankError("Could not clone git repository '%s'!" % repo_path)     
        
    
    ################################################################################################
    # Public interface
    
    def resolve_storages(self):
        """
        Validate that the roots exist in shotgun.
        Communicates with Shotgun.
        
        Returns the root paths from shotgun for each storage.
        
        {
          "primary" : { "description": "Description",
                        "exists_on_disk": False,
                        "defined_in_shotgun": True,
                        "darwin": "/mnt/foo",
                        "win32": "z:\mnt\foo",
                        "linux2": "/mnt/foo"},
                                    
          "textures" : { "description": None,
                         "exists_on_disk": False,
                         "defined_in_shotgun": True,
                         "darwin": None,
                         "win32": "z:\mnt\foo",
                         "linux2": "/mnt/foo"}                                    
         }
                
        :returns: dictionary with storage breakdown, see example above.                                  
        """
        
        return_data = {}
        
        self._log.debug("Checking so that all the local storages are registered...")
        sg_storage = self._sg.find("LocalStorage", [], fields=["code", "linux_path", "mac_path", "windows_path"])

        # make sure that there is a storage in shotgun matching all storages for this config
        sg_storage_codes = [x.get("code") for x in sg_storage]
        cfg_storages = self._roots_data.keys()
                
        for s in cfg_storages:
            
            return_data[s] = { "description": self._roots_data[s].get("description"),
                               "darwin": None,
                               "win32": None,
                               "linux2": None}
            
            if s not in sg_storage_codes:
                return_data[s]["defined_in_shotgun"] = False
                return_data[s]["exists_on_disk"] = False
            else:
                return_data[s]["defined_in_shotgun"] = True
                            
                # find the sg storage paths and add to return data
                for x in sg_storage:
                    
                    if x.get("code") == s:
                        
                        # copy the storage paths across
                        return_data[s]["darwin"] = x.get("mac_path")
                        return_data[s]["linux2"] = x.get("linux_path")
                        return_data[s]["win32"] = x.get("windows_path")
                        
                        # get the local path
                        lookup_dict = {"linux2": "linux_path", "win32": "windows_path", "darwin": "mac_path" }                        
                        local_storage_path = x.get( lookup_dict[sys.platform] )

                        if local_storage_path is None:
                            # shotgun has no path for our local storage
                            return_data[s]["exists_on_disk"] = False
                            
                        elif os.path.exists(local_storage_path):
                            # path is defined but cannot be found
                            return_data[s]["exists_on_disk"] = False
                            
                        else:
                            # path exists! yay!
                            return_data[s]["exists_on_disk"] = True
                                    
        return return_data

    def get_name(self):
        """
        Returns the display name of this config, as defined in the manifest
        
        :returns: string
        """
        return self._manifest.get("display_name")
        
    def get_description(self):
        """
        Returns the description of the config, as defined in the manifest
        
        :returns string
        """
        return self._manifest.get("description")

    def create_configuration(self, target_path):
        """
        Creates the configuration folder in the target path
        """
        old_umask = os.umask(0)
        try:

            if self._config_mode == "git":
                # clone the config into place
                self._log.info("Cloning git configuration into '%s'..." % target_path)
                self._clone_git_repo(self._config_uri, target_path)
            else:
                # copy the config from its source location into place
                _copy_folder(self._log, self._cfg_folder, target_path )

        finally:
            os.umask(old_umask)

    
