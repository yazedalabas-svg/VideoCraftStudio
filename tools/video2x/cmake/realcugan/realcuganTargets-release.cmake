#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "realcugan::librealcugan-ncnn-vulkan" for configuration "Release"
set_property(TARGET realcugan::librealcugan-ncnn-vulkan APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(realcugan::librealcugan-ncnn-vulkan PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/./librealcugan-ncnn-vulkan.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "ncnn"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/./librealcugan-ncnn-vulkan.dll"
  )

list(APPEND _cmake_import_check_targets realcugan::librealcugan-ncnn-vulkan )
list(APPEND _cmake_import_check_files_for_realcugan::librealcugan-ncnn-vulkan "${_IMPORT_PREFIX}/./librealcugan-ncnn-vulkan.lib" "${_IMPORT_PREFIX}/./librealcugan-ncnn-vulkan.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
