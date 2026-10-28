import unittest

from cogs.utility import DiceGameView


class DiceGameViewTests(unittest.TestCase):
    def test_creator_is_banker_and_first_player(self):
        view = DiceGameView(banker_id=123, banker_name="Host", player_limit=3, sides=6)
        self.assertEqual(view.banker_id, 123)
        self.assertEqual(view.players, {123: "Host"})
        self.assertIn("莊家：Host", view.render())
        self.assertIn("人數：1/3", view.render())

    def test_render_shows_results_and_ties(self):
        view = DiceGameView(banker_id=123, banker_name="Host", player_limit=2, sides=6)
        view.players[456] = "Guest"
        output = view.render(results=[("Host", 6), ("Guest", 6)])
        self.assertIn("平手：Host、Guest", output)
        self.assertIn("最高點數：**6**", output)

    def test_join_and_roll_controls_exist(self):
        view = DiceGameView(banker_id=123, banker_name="Host", player_limit=2, sides=6)
        labels = {item.label for item in view.children}
        self.assertIn("加入遊戲", labels)
        self.assertIn("開始擲骰（莊家）", labels)


if __name__ == "__main__":
    unittest.main()
