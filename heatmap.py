import argparse
import colorsys
import os

from PIL import Image
import math
import numpy as np
import json
import pyopencl as cl
import pyopencl.array


# Импортируем размеры спаршенной карты
from cian_map_parser import lat_start, lon_start, lat_end, lon_end

MIN_LAT = lat_start
MAX_LAT = lat_end
MIN_LON = lon_start
MAX_LON = lon_end
MAX_X = 0
MAX_Y = 0

# Сколько строк загружать из файла 0 - все
LIMIT = 0
# Желаемое разрешение (пикселей на 1 км)
RESOLUTION = 25
# Рисовать точки объектов на оверлее?
DRAW_DOTS = True
# ограничение расстояния предсказания
IGNORE_DIST = 0.025


def ll_to_pixel(lat, lon):
    adj_lat = lat - MIN_LAT
    adj_lon = lon - MIN_LON

    delta_lat = MAX_LAT - MIN_LAT
    delta_lon = MAX_LON - MIN_LON

    # x - lon, y - lat
    # 0,0 - это MIN_LON, MAX_LAT

    lon_frac = adj_lon / delta_lon
    lat_frac = adj_lat / delta_lat

    x = int(lon_frac * MAX_X)
    y = int((1 - lat_frac) * MAX_Y)

    return x, y


def distance_kilometers(lat1, lon1, lat2, lon2):
    return math.ceil(3958 * 3.1415926 * math.sqrt(
        (lat2 - lat1) * (lat2 - lat1) +
        math.cos(lat2 / 57.29578) * math.cos(lat1 / 57.29578) * (lon2 - lon1) * (lon2 - lon1)
    ) / 180)


def load_prices(fs):
    raw_prices = []
    seen = set()
    i = 0
    for f in fs:
        with open(f, encoding='utf-8') as inf:
            for line in inf:
                if 0 < LIMIT < i:
                    break
                i += 1
                if not line[0].isdigit():
                    continue
                sq_price, bedrooms, apt_id, lon, lat, _, _, _, _ = line.strip().split(';')
                if apt_id in seen:
                    continue
                else:
                    seen.add(apt_id)
                sq_price, bedrooms = float(sq_price), int(bedrooms)
                raw_prices.append([sq_price, bedrooms, float(lat), float(lon)])
    print("Загружено %s строк" % i)
    return raw_prices


def hsv2rgb(h, s, v):
    rgb = colorsys.hsv_to_rgb(h / 360, s / 100, v / 100)
    rgb_non_norm = tuple(int(round(i * 255)) for i in rgb)
    rgbhex = '#%02x%02x%02x' % rgb_non_norm
    return rgb_non_norm, rgbhex


def bucket_color(buckets):
    num_buckets = len(buckets)
    colorsteps = math.ceil(300 / num_buckets)
    i = 0
    for step in range(0, 300, colorsteps):
        bval = buckets[i]
        buckets[i] = [bval]
        rgb = hsv2rgb(step, 100, 100)
        buckets[i].append(rgb[0])
        buckets[i].append(rgb[1])
        i += 1

    return buckets


def color(val, buckets):
    if val is None:
        return (255, 255, 255, 0)
    for price, rgb_color, hex_color in buckets:
        if val > price:
            return rgb_color
    return buckets[-1][1]


gaussian_variance = IGNORE_DIST / 2
gaussian_a = 1 / (gaussian_variance * math.sqrt(2 * math.pi))
gaussian_negative_inverse_twice_variance_squared = \
    -1 / (2 * gaussian_variance * gaussian_variance)


def start(filename):
    global MAX_X, MAX_Y
    print("Вычисляем размер картинки")
    dist_lat = distance_kilometers(MIN_LAT, MIN_LON, MAX_LAT, MIN_LON)
    dist_lon = distance_kilometers(MIN_LAT, MIN_LON, MIN_LAT, MAX_LON)
    MAX_X = int(dist_lon * RESOLUTION)
    MAX_Y = int(dist_lat * RESOLUTION)

    print("Загружаем данные из " + filename)
    priced_points = load_prices([filename])

    # Подготавливаем входные данные для OpenCL
    nparr = np.array(priced_points, dtype=np.float32)
    prices = np.array(nparr[:, 0], dtype=np.float32)
    lats = np.array(nparr[:, 2], dtype=np.float32)
    lons = np.array(nparr[:, 3], dtype=np.float32)
    cl_min_lat = np.float32(MIN_LAT)
    cl_min_lon = np.float32(MIN_LON)
    cl_max_lat = np.float32(MAX_LAT)
    cl_max_lon = np.float32(MAX_LON)
    cl_gaussian_a = np.float32(gaussian_a)
    cl_gaussian_negative_inverse_twice_variance_squared = \
        np.float32(gaussian_negative_inverse_twice_variance_squared)

    cl_max_x = np.int32(MAX_X)
    cl_max_y = np.int32(MAX_Y)

    items = np.int32(prices.size)

    # Массив для выходных данных
    cord_val = np.dtype([('x', np.int32), ('y', np.int32), ('val', np.float32)])
    output = np.zeros(MAX_X * MAX_Y, dtype=cord_val)

    # инициализация openCL
    # Если есть несколько девайсов, выбираем первый
    platform = cl.get_platforms()[0]
    device = platform.get_devices()[0]
    context = cl.Context([device])

    # или можем спросить у пользователя, какой нужен
    # context = cl.create_some_context()

    # Создаем очередь
    # queue = cl.CommandQueue(context, properties=cl.command_queue_properties.PROFILING_ENABLE)
    queue = cl.CommandQueue(context)

    # Инициализируем входящие буферы
    prices_buf = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=prices)
    lats_buf = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=lats)
    lons_buf = cl.Buffer(context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=lons)

    # Инициализируем исходящие буферы
    output_buf = cl.array.to_device(queue, output)

    print("Вычисляем тепловую карту")
    prices = {}
    # Загружаем OpenCL програму
    program = cl.Program(context, open('heatmap.cl', 'r', encoding='utf-8').read()).build()
    # запускаем програму
    launch = program.heatmap(
        queue,
        # globals
        (MAX_X, MAX_Y), None,
        # input globals
        prices_buf, lats_buf, lons_buf,
        # input constants
        items, cl_min_lat, cl_min_lon, cl_max_lat, cl_max_lon,
        cl_gaussian_a, cl_gaussian_negative_inverse_twice_variance_squared,
        cl_max_x, cl_max_y,
        # output
        output_buf.data
    )
    launch.wait()

    print("Разбиваем на группы")
    # Забираем данные и исходящего буфера
    # и конвертируем в сет [x,y]=val
    out_rs = output_buf.reshape(MAX_Y * MAX_X).get()
    for coords in out_rs:
        if coords[2] > 0.0:
            prices[coords[0], coords[1]] = coords[2]
        else:
            prices[coords[0], coords[1]] = None

    # Создаем группы цен
    # желательно тоже перевести на OpenCL, т.к. на больших данных
    # не очень быстро считается
    all_priced_areas = sorted(set(filter(None, prices.values())))
    total_priced_area = len(all_priced_areas)
    buckets = []
    divisions = 9  # число делений
    stride = total_priced_area / (divisions + 1)
    next_i = int(stride)
    error_i = stride - next_i
    for i, val in enumerate(all_priced_areas):
        if i == next_i:
            buckets.append(float(val))
            delta_i = stride + error_i
            next_i += int(delta_i)
            error_i = delta_i - int(delta_i)

    buckets.reverse()
    # вычисляем цвета для групп
    buckets = bucket_color(buckets)

    # Создаем оверлей тепловой карты
    image = Image.new('RGBA', (MAX_X, MAX_Y))
    image_object = image.load()
    for x in range(MAX_X):
        for y in range(MAX_Y):
            image_object[x, y] = color(prices[x, y], buckets)

    if DRAW_DOTS:
        for _, _, lat, lon in priced_points:
            x, y = ll_to_pixel(lat, lon)
            if 0 <= x < MAX_X and 0 <= y < MAX_Y:
                image_object[x, y] = (0, 0, 0)

    if not os.path.exists("results"):
        os.makedirs("results")
    out_fname = "results/" + filename + "." + str(MAX_X)
    image.save(out_fname + ".png", "PNG")
    with open(out_fname + ".metadata.json", "w") as outf:
        outf.write(json.dumps({
            "buckets": buckets,
            "n": len(priced_points)}))
    print("Размер изображения: %i x %i " % (MAX_X, MAX_Y))
    print("Созданные файлы:\n" + out_fname + ".png\n" + out_fname + ".metadata.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Создание тепловой карты.')
    parser.add_argument('-f', '--file', required=True, help='Имя файла с исходными данными')
    parser.add_argument('-r', '--res', help='Желаемое разрешение (пикселей на 1 км)',
                        default=10, type=int)
    parser.add_argument('-i', '--ignore', help='Ограничение расстояния предсказания',
                        default=0.025, type=float)
    parser.add_argument('-l', '--limit', help='Сколько строчек загрузить из файла (0 - все)',
                        default=0, type=int)
    parser.add_argument('-d', '--drawdots', help='Отобразить точки объектов на карте',
                        action='store_true')

    args = parser.parse_args()
    file = args.file
    LIMIT = args.limit
    RESOLUTION = args.res
    IGNORE_DIST = args.ignore
    DRAW_DOTS = args.drawdots
    start(file)
