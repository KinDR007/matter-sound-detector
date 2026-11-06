#ifndef TSD_MODEL_H
#define TSD_MODEL_H

/* Generated model parameters: int8 (CMSIS-NN) or float variant. */
#ifdef CONFIG_TSD_MODEL_INT8
#include "model_data_q8.h"
#else
#include "model_data.h"
#endif

#endif
