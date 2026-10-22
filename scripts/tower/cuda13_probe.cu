// Compile now; run only after the administrator activates the new driver.
#include <cuda_runtime.h>
#include <cstdio>
__global__ void increment(int* value) { *value += 1; }
int main() {
    int count = 0;
    cudaError_t rc = cudaGetDeviceCount(&count);
    if (rc != cudaSuccess || count < 1) {
        std::fprintf(stderr, "CUDA unavailable: %s (devices=%d)\n", cudaGetErrorString(rc), count);
        return 1;
    }
    int *device = nullptr;
    rc = cudaMalloc(&device, sizeof(int));
    if (rc != cudaSuccess) return 2;
    int result = 41;
    rc = cudaMemcpy(device, &result, sizeof(int), cudaMemcpyHostToDevice);
    if (rc == cudaSuccess) {
        increment<<<1,1>>>(device);
        rc = cudaDeviceSynchronize();
    }
    if (rc == cudaSuccess) rc = cudaMemcpy(&result, device, sizeof(int), cudaMemcpyDeviceToHost);
    cudaFree(device);
    if (rc != cudaSuccess || result != 42) return 3;
    std::printf("PASS: devices=%d, kernel result=%d\n", count, result);
    return 0;
}
