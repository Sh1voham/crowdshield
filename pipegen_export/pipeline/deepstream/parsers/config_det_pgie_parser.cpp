// Custom detection (bbox) parser (generated).
// Scaffold validates the output layer and exposes the raw tensor(s); the DECODE
// block (per-model) populates `objectList` with NvDsInferObjectDetectionInfo.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "nvdsinfer_custom_impl.h"

extern "C" bool ParseConfigDetPgie(
    std::vector<NvDsInferLayerInfo> const& outputLayersInfo,
    NvDsInferNetworkInfo const& networkInfo,
    NvDsInferParseDetectionParams const& detectionParams,
    std::vector<NvDsInferObjectDetectionInfo>& objectList);

CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(ParseConfigDetPgie);

extern "C" bool ParseConfigDetPgie(
    std::vector<NvDsInferLayerInfo> const& outputLayersInfo,
    NvDsInferNetworkInfo const& networkInfo,
    NvDsInferParseDetectionParams const& detectionParams,
    std::vector<NvDsInferObjectDetectionInfo>& objectList)
{
    if (outputLayersInfo.empty()) return false;
    auto const& layer0 = outputLayersInfo[0];

    const unsigned numClasses = detectionParams.numClassesConfigured;
    const float netW = static_cast<float>(networkInfo.width);
    const float netH = static_cast<float>(networkInfo.height);
    const float* data = static_cast<const float*>(layer0.buffer);

    static bool logged = false;
    if (!logged) {
        logged = true;
        fprintf(stderr, "[YOLO11s_det] first call: layer=%s numDims=%d "
                "networkInput=%.0fx%.0f numClasses=%u outputs=%zu\n",
                layer0.layerName ? layer0.layerName : "?",
                layer0.inferDims.numDims, netW, netH, numClasses,
                outputLayersInfo.size());
    }

    // ==== BEGIN GENERATED DECODE ====
    // Output tensor shape (batch stripped): [84, 8400]
    // inferDims: numDims=2, d[0]=84, d[1]=8400
    // Row layout: rows 0-3 = cx, cy, w, h (in network-input pixels)
    //             rows 4..83 = class scores (post-sigmoid probabilities)
    // We need to transpose: for each of the numDetections anchors, gather
    // cx/cy/w/h and class scores from column-major layout.

    const unsigned int featDim = layer0.inferDims.d[0];   // 84 = 4 + numClasses
    const unsigned int numDetections = layer0.inferDims.d[1]; // 8400

    if (featDim < 4u + numClasses) {
        fprintf(stderr, "[YOLO11s_det] ERROR: featDim %u < 4 + numClasses %u\n",
                featDim, numClasses);
        return false;
    }

    for (unsigned int i = 0; i < numDetections; ++i) {
        // Transpose access: data[row * numDetections + i]
        float cx = data[0u * numDetections + i];
        float cy = data[1u * numDetections + i];
        float bw = data[2u * numDetections + i];
        float bh = data[3u * numDetections + i];

        // Find best class
        float maxScore = 0.0f;
        unsigned int bestClass = 0;
        for (unsigned int c = 0; c < numClasses; ++c) {
            float s = data[(4u + c) * numDetections + i];
            if (s > maxScore) {
                maxScore = s;
                bestClass = c;
            }
        }

        // Per-class threshold
        float thr = (bestClass < detectionParams.perClassPreclusterThreshold.size())
                     ? detectionParams.perClassPreclusterThreshold[bestClass]
                     : 0.25f;
        if (maxScore < thr) continue;

        // Convert cx,cy,w,h (network-input pixels) to left,top,width,height
        float left   = cx - bw * 0.5f;
        float top    = cy - bh * 0.5f;
        float right  = cx + bw * 0.5f;
        float bottom = cy + bh * 0.5f;

        // Clamp to network input
        left   = std::max(0.0f, std::min(left,   netW));
        top    = std::max(0.0f, std::min(top,    netH));
        right  = std::max(0.0f, std::min(right,  netW));
        bottom = std::max(0.0f, std::min(bottom, netH));

        float w = right - left;
        float h = bottom - top;
        if (w <= 1.0f || h <= 1.0f) continue;

        NvDsInferObjectDetectionInfo obj;
        obj.classId = bestClass;
        obj.left = left;
        obj.top = top;
        obj.width = w;
        obj.height = h;
        obj.detectionConfidence = maxScore;
        objectList.push_back(obj);
    }
    // ==== END GENERATED DECODE ====
    return true;
}