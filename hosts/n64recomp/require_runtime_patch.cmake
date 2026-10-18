# Check the required dependency fix before compiling any runtime or guest code.
function(n64host_require_runtime_patch runtime_dir patch_path)
  execute_process(
    COMMAND git -C "${runtime_dir}" apply --reverse --check "${patch_path}"
    RESULT_VARIABLE patch_result OUTPUT_QUIET ERROR_QUIET)
  if(NOT patch_result EQUAL 0)
    message(FATAL_ERROR
      "Required N64ModernRuntime queue removal patch is absent or incompatible. "
      "Apply ${patch_path} in ${runtime_dir} before building the host.")
  endif()
endfunction()
