import bs4

f = open('archive.php', 'r')
soup = bs4.BeautifulSoup(f.read(), 'lxml')

def get_last_volume():
    return soup.findAll('div', class_="container")[-1]

# for i in soup.findAll('div', class_="col-lg-12"):
#     bq = i.find('blockquote')
#     if bq and "Vol 5" in bq.text:
#         s = i.find('tbody')
#         print(len(s.findAll('tr')))
#         input()
#         s.append("hello")
#         print(s)
#         break
    # if ":
    #     print(i)
# print(soup.prettify())
get_last_column()
f.close()
