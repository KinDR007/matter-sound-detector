/*
 * Copyright (c) 2021 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

/**
 *    @file
 *          Example project configuration file for CHIP.
 *
 *          This is a place to put application or project-specific overrides
 *          to the default configuration values for general CHIP features.
 *
 */

#pragma once

/* Basic Information SoftwareVersionString "1.0.0" from firmware/VERSION; NCS default is
 * APP_VERSION_EXTENDED_STRING ("1.0.0+0"). scripts/fw.sh sets the same string in matter.ota. */
#include <app_version.h>
#define CONFIG_CHIP_DEVICE_SOFTWARE_VERSION_STRING APP_VERSION_STRING
