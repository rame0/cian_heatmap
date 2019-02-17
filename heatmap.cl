typedef struct {
  unsigned int x;
  unsigned int y;
  float val;
} cordVal;

__kernel void heatmap(
    __global float* prices,         // 0..N float prices
    __global float* lats,           // 0..N float lattitudes
    __global float* lons,           // 0..N float longitudes
    __const int items,              // number N of items in arrays
    __const float min_lat,
    __const float min_lon,
    __const float max_lat,
    __const float max_lon,
    __const float gaussian_a,
    __const float GNITVS,

    __const int max_x,
    __const int max_y,

    __global cordVal* all_priced_areas   // output

    )
{
    int x = get_global_id(0);
    int y = get_global_id(1);

    int global_size_0 = get_global_size(0);

    // calc pixel to lat and lon
    float delta_lat = max_lat - min_lat;
    float delta_lon = max_lon - min_lon;
    float x_frac = (float) x / (float)max_x;
    float y_frac = (float) y / (float)max_y;

    float lon = min_lon + (x_frac * delta_lon);
    float lat = max_lat - (y_frac * delta_lat);
    //float lat = min_lat + (y_frac * delta_lat);

    // calc gausian
    float num = 0;
    float dnm = 0;
    int significant_values = 0;

    for(int i = 0; i < items; i++){
        float price = prices[i];
        float plat = lats[i];
        float plon = lons[i];

        //printf((__constant char *)"%f\n", price);

        float distance_squared = pow(lat - plat, 2) + pow(lon - plon, 2);
        float weight = gaussian_a * exp(distance_squared * GNITVS);

        num += (price * weight);
        dnm += weight;

        if( weight > 1.99999){
             significant_values+=1;
        }
    }

    //printf((__constant char *)"\n\n");

    int index = y * global_size_0 + x;
    all_priced_areas[index].x = x;
    all_priced_areas[index].y = y;

    // ignore any averages that don't take into account at least
    // N data points with significant weight
    if(significant_values >= 5){
        all_priced_areas[index].val = (num / dnm);
    }else{
        all_priced_areas[index].val = -1;
    }

    //printf((__constant char *)"Size of prices: %i \n", items);
    //printf((__constant char *)"num: %f, dnm: %f\n", num, dnm);
    //printf((__constant char *)"Coord %ix%i, value: %f\nPosition %i\n", x, y, num / dnm, x * max_x + y);
}