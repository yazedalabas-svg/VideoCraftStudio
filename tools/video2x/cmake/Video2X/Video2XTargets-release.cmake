#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "Video2X::libvideo2x" for configuration "Release"
set_property(TARGET Video2X::libvideo2x APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(Video2X::libvideo2x PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/./libvideo2x.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "ncnn;realesrgan::librealesrgan-ncnn-vulkan;realcugan::librealcugan-ncnn-vulkan;rife::librife-ncnn-vulkan"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/./libvideo2x.dll"
  )

list(APPEND _cmake_import_check_targets Video2X::libvideo2x )
list(APPEND _cmake_import_check_files_for_Video2X::libvideo2x "${_IMPORT_PREFIX}/./libvideo2x.lib" "${_IMPORT_PREFIX}/./libvideo2x.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
