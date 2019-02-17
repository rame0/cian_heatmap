import datetime
import math
import re
import codecs
import requests
import time
from random import randint
from concurrent.futures import ProcessPoolExecutor
import concurrent.futures

url_api = 'http://map.cian.ru/ajax/map/roundabout/?deal_type=sale&engine_version=2&object_type[0]=3' \
          + '&offer_type=suburban&deal_type=2&add_clusters_sizes=1&allow_precision_correction=0'

lat_start = 55.370371410184795
lon_start = 36.80407776498748
lat_end = 56.09563680081344
lon_end = 38.94216370248747

# Шаг парсинга
step_global = 0.005

# ставим здесь шаг, на котором остановились, для продолжения парсинга
left_start = 0

req_id = 1466708040958
errors_url = []


def distance_kilometers(lat1, lon1, lat2, lon2):
    return math.ceil(3958 * 3.1415926 * math.sqrt(
        (lat2 - lat1) * (lat2 - lat1) + math.cos(lat2 / 57.29578) * math.cos(lat1 / 57.29578) * (lon2 - lon1) * (
                lon2 - lon1)) / 180)


def get_info(url, *args):
    time.sleep(randint(0, 100) / 100)
    if len(args) > 0:
        lat_box_start, lon_box_start, step = args
        lat_box_stop = lat_box_start + step
        lon_box_stop = lon_box_start + step
        url = url_api + '&bbox={0},{1},{2},{3}' \
            .format(lat_box_start, lon_box_start, lat_box_stop, lon_box_stop)
    results = []
    try:
        parsed = requests.get(url).json()
    except Exception:
        # print('Не удалось спарсить url: {}'.format(url))
        errors_url.append(url)
        return results
    if parsed['status'] != 'ok':
        print('Слишком много объектов в блоке %s' % url)
        if len(args) > 0:
            results += get_info(url_api, lat_box_start, lon_box_start, step / 2)
            results += get_info(url_api, lat_box_start + step / 2, lon_box_start, step / 2)
            results += get_info(url_api, lat_box_start, lon_box_start + step / 2, step / 2)
            results += get_info(url_api, lat_box_start + step / 2, lon_box_start + step / 2, step / 2)
    elif parsed['data']['offers_count'] == 0:
        print('В блоке нет объектов')
    else:
        data = parsed['data']['points']
        offer_type = parsed['offer_type']
        for coord, flats in data.items():
            # interested only in Moscow
            if flats['isMoscowOrArea'] == 'false':
                continue
            coordinates = coord.split(' ')
            # coordinates = '[{},{}]'.format(coordinates[0], coordinates[1])
            address = flats['content']['text']
            flats = flats['offers']
            for flat in flats:
                lat = coordinates[0]
                lon = coordinates[1]
                price = flat['price_rur']
                property_type = flat['property_type']
                index = flat['id']
                rooms = flat['link_text'][0]
                square_orig = flat['link_text'][1]
                floor = flat['link_text'][3]

                square_parsed = re.match(r'(\d+[\.\d]*)\s*(.*?)$', square_orig)
                square = float(square_parsed.group(1))
                if square_parsed.group(2) == 'га':
                    square = square * 100
                if square < 1:
                    continue
                square_price = round(float(price) / square, 2)

                square = round(square, 2)

                # цена кв.м., тип объекта, №объявления, Lon, Lat, Цена объекта, площадь, ориентир, ориг. запись площади
                result = [str(square_price), property_type, index, lon, lat, price, str(square), address, square_orig]

                results.append(";".join(result))
    return results


def error_url(file):
    fname = datetime.datetime.now().strftime("%Y-%m-%d")
    inp = open(file, mode='r+', encoding='utf-8')
    lines = inp.readlines()
    inp.truncate(0)
    inp.close()
    for line in lines:
        if line.startswith('ERROR:root:Error1'):
            indx = line.find('url: ')
            url = line[indx + 5:len(line) - 2]
            results = get_info(url)
            if len(results) > 0:
                file = codecs.open(fname + ".csv", "a", "utf-8")
                file.write('\n'.join(results) + '\n')
                file.close()
                print("Добавлено %s объектов" % len(results))


def main():
    fname = datetime.datetime.now().strftime("%Y-%m-%d")
    lon_coord = lon_start
    total_flats = 0
    start = time.time()

    dist_lat = distance_kilometers(lat_start, lon_start, lat_end, lon_start)
    dist_lon = distance_kilometers(lat_start, lon_start, lat_start, lon_end)

    # для корректности дальнейшего построения heatmap
    # размер по широте должен быть больше чем размер по долготе
    if dist_lat > dist_lon:
        print("Увеличти размеры по латитуде!")
        exit()

    lat_range = range(math.floor((lat_end - step_global - lat_start) / step_global))
    if left_start > 0:
        lon_range = range(left_start,
                          math.floor((lon_end - step_global - lon_start) / step_global))
    else:
        lon_range = range(math.floor((lon_end - step_global - lon_start) / step_global))

    print('Шагов по lon: %s, шагов по lat: %s' % (lon_range, lat_range))
    lat_coords = [lat_start + i * step_global for i in lat_range]

    for i in lon_range:
        results = []
        # async work
        with ProcessPoolExecutor(max_workers=10) as executor:
            future_results = {
                executor.submit(
                    get_info, url_api, lat_cord, lon_coord, step_global
                ): lat_cord for lat_cord in lat_coords
            }
            for future in concurrent.futures.as_completed(future_results):
                results += future.result()
        # sync work
        # for lat_cord in lat_coords:
        #     results += get_info(url_api, lat_cord, lon_coord, step_global)

        if len(results) > 0:
            file = codecs.open(fname + ".csv", "a", "utf-8")
            file.write('\n'.join(results) + '\n')
            file.close()
            total_flats += len(results)

        end = time.time()
        print("Добавлено %s объектов" % len(results))
        print("Времени прошло: {}, на шаге {}/{}".format(end - start, i, lon_range.stop))
        print("Всего объектов: %s" % total_flats)
        lon_coord += step_global

        # Еще раз попытаться собрать URL вызвавшиъ исключение
        for url in errors_url:
            time.sleep(randint(0, 100) / 10)
            results = get_info(url)
            if len(results) > 0:
                file = codecs.open(fname + ".csv", "a", "utf-8")
                file.write('\n'.join(results) + '\n')
                file.close()
                total_flats += len(results)
                print("Добавлено %s объектов" % len(results))


if __name__ == '__main__':
    main()
