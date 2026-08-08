#----------------------------------------------------------------
# Generated CMake target import file for configuration "Release".
#----------------------------------------------------------------

# Commands may need to know the format version.
set(CMAKE_IMPORT_FILE_VERSION 1)

# Import target "realesrgan::librealesrgan-ncnn-vulkan" for configuration "Release"
set_property(TARGET realesrgan::librealesrgan-ncnn-vulkan APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties(realesrgan::librealesrgan-ncnn-vulkan PROPERTIES
  IMPORTED_IMPLIB_RELEASE "${_IMPORT_PREFIX}/./librealesrgan-ncnn-vulkan.lib"
  IMPORTED_LINK_DEPENDENT_LIBRARIES_RELEASE "ncnn"
  IMPORTED_LOCATION_RELEASE "${_IMPORT_PREFIX}/./librealesrgan-ncnn-vulkan.dll"
  )

list(APPEND _cmake_import_check_targets realesrgan::librealesrgan-ncnn-vulkan )
list(APPEND _cmake_import_check_files_for_realesrgan::librealesrgan-ncnn-vulkan "${_IMPORT_PREFIX}/./librealesrgan-ncnn-vulkan.lib" "${_IMPORT_PREFIX}/./librealesrgan-ncnn-vulkan.dll" )

# Commands beyond this point should not need to know the version.
set(CMAKE_IMPORT_FILE_VERSION)
