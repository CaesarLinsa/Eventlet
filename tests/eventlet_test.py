import eventlet


def test1():
    print("1")
    print("2")


def test2():
    print("3")
    print("4")


if __name__ == '__main__':
    g1 = eventlet.spawn(test1)
    g2 = eventlet.spawn(test2)
    eventlet.sleep(0)
    print("5")
