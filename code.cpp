for (int i = 0; i < RPoints.size(); ++i)
{
	std::vector<double> RP = { RPoints[i].points.x,RPoints[i].points.y,RPoints[i].points.z,RPoints[i].points.rx,RPoints[i].points.ry,RPoints[i].points.rz };
	if (RPoints[i].type.is_LINEAR)
	{
		CDescartesPoint pt;
		pt.x = RPoints[i].points.x;
		pt.y = RPoints[i].points.y;
		pt.z = RPoints[i].points.z;
		pt.rx = RPoints[i].points.rx;
		pt.ry = RPoints[i].points.ry;
		pt.rz = RPoints[i].points.rz;
		std::string userparam{ "User=0" };
		std::string toolparam{ "Tool=0" };
		std::string speedlparam{ "SpeedL=10" };
		std::string acclparam{ "AccL=1" };
		//std::string cpparam{ "CP=100" };
		dobot->MovL(pt, userparam, toolparam, speedlparam, acclparam);
		dobot->SetCP();  //CP(100)
		//执行直线运动
		// RobotMoveL(RP);//需要改
		size = size + 1;
	}
}
for (int i = size; i < RPoints.size(); i=i+2) {
	if (i > RPoints.size()-2) {
		return;
	}
	else {
		if (RPoints[i].type.is_CIRCULAR)
		{
			CDescartesPoint pt1;
			pt1.x = RPoints[i].points.x;
			pt1.y = RPoints[i].points.y;
			pt1.z = RPoints[i].points.z;
			pt1.rx = RPoints[i].points.rx;
			pt1.ry = RPoints[i].points.ry;
			pt1.rz = RPoints[i].points.rz;
			CDescartesPoint pt2;
			pt2.x = RPoints[i+1].points.x;
			pt2.y = RPoints[i+1].points.y;
			pt2.z = RPoints[i+1].points.z;
			pt2.rx = RPoints[i+1].points.rx;
			pt2.ry = RPoints[i+1].points.ry;
			pt2.rz = RPoints[i+1].points.rz;
			std::string userparam{ "User=0" };
			std::string toolparam{ "Tool=0" };
			std::string speedlparam{ "SpeedL=10" };
			std::string acclparam{ "AccL=1" };

			dobot->Arc(pt1, pt2, userparam, toolparam, speedlparam, acclparam);
			dobot->SetCP();  //CP(100)
			//执行圆弧运动
			// RobotMoveC(RP);//需要改
		}
	}
}