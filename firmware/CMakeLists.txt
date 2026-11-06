#
# Copyright (c) 2021 Nordic Semiconductor ASA
#
# SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
#

cmake_minimum_required(VERSION 3.20.0)

find_package(Zephyr HINTS $ENV{ZEPHYR_BASE})

project(thread-sound-detector)

# Enable GNU STD support.
include(${ZEPHYR_CONNECTEDHOMEIP_MODULE_DIR}/config/nrfconnect/app/enable-gnu-std.cmake)

# NORDIC SDK APP START

# Include all source files that are located in the Matter common directory.
include(${ZEPHYR_NRF_MODULE_DIR}/samples/matter/common/cmake/source_common.cmake)

# Include Data Model utils
include(${ZEPHYR_NRF_MODULE_DIR}/samples/matter/common/cmake/data_model.cmake)

include(${ZEPHYR_NRF_MODULE_DIR}/samples/matter/common/cmake/zap_helpers.cmake)
ncs_get_zap_parent_dir(ZAP_PARENT_DIR)

target_include_directories(app PRIVATE
  src
  ${ZAP_PARENT_DIR}
)

target_sources(app PRIVATE
  src/app_task.cpp
  src/main.cpp
  src/label_provider.cpp
  src/device_info.cpp
  src/audio.c
  src/dsp.c
  src/detector.c
)

if(CONFIG_TSD_MODEL_INT8)
  target_sources(app PRIVATE src/model_q8.c)
endif()

# Inference speed: the rest of the image is size-optimized
set_source_files_properties(src/dsp.c src/model_q8.c PROPERTIES COMPILE_OPTIONS "-O3")

ncs_configure_data_model()
# NORDIC SDK APP END
