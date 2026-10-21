# Check the required dependency fix before compiling any runtime or guest code.
# The optional third argument names the patch in the error (default: queue removal).
function(n64host_require_runtime_patch runtime_dir patch_path)
  set(label "queue removal")
  if(ARGC GREATER 2)
    set(label "${ARGV2}")
  endif()
  execute_process(
    COMMAND git -C "${runtime_dir}" apply --reverse --check "${patch_path}"
    RESULT_VARIABLE patch_result OUTPUT_QUIET ERROR_QUIET)
  if(NOT patch_result EQUAL 0)
    message(FATAL_ERROR
      "Required N64ModernRuntime ${label} patch is absent or incompatible. "
      "Apply ${patch_path} in ${runtime_dir} before building the host.")
  endif()
endfunction()
