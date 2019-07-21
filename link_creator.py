import requests
import bs4

ROOT_URL = 'http://gjmsweb.com/archives/'
START_YEAR = 2015
END_YEAR = 2019
VOLUME_START = 1
VOLUME_END = 5
ISSUE_START = 1
ISSUE_END = 5


def main():
    for year in range(START_YEAR, END_YEAR+1):
        for issue in range(ISSUE_START, ISSUE_END):
            print(ROOT_URL + str(year) + "/" + "Volume {}".format((year-START_YEAR)+1) + "/Issue {}, {}".format(issue, year))

            # r = requests.get(ROOT_URL + str(year) + "/" + "Issue {}, {}".format(issue, year))
            # soup = bs4.BeautifulSoup()
main()
