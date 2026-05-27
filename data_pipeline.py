import pandas as pd
import os

class DataPipeline:
    def __init__(self, file_path):
        self.file_path = file_path
        self.df = self._load_data()

    def _load_data(self):
        """
        Tải dữ liệu từ file CSV hoặc Excel.
        """
        if not os.path.exists(self.file_path):
            print(f"File {self.file_path} chưa tồn tại. Sẽ tạo mới khi lưu.")
            return pd.DataFrame(columns=["Username"])
        
        if self.file_path.endswith('.csv'):
            return pd.read_csv(self.file_path)
        elif self.file_path.endswith('.xlsx'):
            return pd.read_excel(self.file_path)
        else:
            raise ValueError("Định dạng file không hỗ trợ (chỉ hỗ trợ .csv hoặc .xlsx)")

    def update_account_data(self, results_list):
        """
        Cập nhật danh sách kết quả vào DataFrame.
        results_list: Danh sách các Dictionary chứa 'Username' và các thông tin mới.
        """
        for result in results_list:
            username = result.get("Username")
            if not username:
                continue

            # Nếu Username đã tồn tại, cập nhật dòng đó
            if username in self.df["Username"].values:
                index = self.df[self.df["Username"] == username].index[0]
                for key, value in result.items():
                    if key != "Username":
                        self.df.at[index, key] = value
            else:
                # Nếu chưa tồn tại, thêm dòng mới
                new_row = pd.DataFrame([result])
                self.df = pd.concat([self.df, new_row], ignore_index=True)
        
        print(f"Đã cập nhật dữ liệu cho {len(results_list)} tài khoản.")

    def save(self):
        """
        Lưu DataFrame lại file gốc.
        """
        try:
            if self.file_path.endswith('.csv'):
                self.df.to_csv(self.file_path, index=False)
            elif self.file_path.endswith('.xlsx'):
                self.df.to_excel(self.file_path, index=False)
            print(f"Dữ liệu đã được lưu an toàn tại: {self.file_path}")
        except Exception as e:
            print(f"Lỗi khi lưu file: {e}")

if __name__ == "__main__":
    # Ví dụ sử dụng:
    # pipeline = DataPipeline("accounts_data.xlsx")
    # data_to_update = [
    #     {"Username": "user1", "Country": "US", "KYC": "Completed", "Followers": "10.5K"},
    #     {"Username": "user2", "Country": "UK", "KYC": "Action Required"}
    # ]
    # pipeline.update_account_data(data_to_update)
    # pipeline.save()
    print("DataPipeline đã sẵn sàng.")
