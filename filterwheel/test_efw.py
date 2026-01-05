from filterwheel import FilterWheel

fw = FilterWheel()
print(fw.position)
fw.goto('g')
fw.wait_for_move()
print('at g')
fw.goto('Ha')
fw.wait_for_move()
print('at Ha')

